# Copyright 2022 Dominik Sekotill <dom.sekotill@kodo.org.uk>
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Coordinate receiving and sending raw messages with a filter and Session object

The primary class in this module (`Runner`) is intended to be used with an
`anyio.abc.Listener`, which can be obtained, for instance, from
`anyio.create_tcp_listener()`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from warnings import warn

import anyio.abc
from anyio.streams.stapled import StapledObjectStream
from async_generator import aclosing

from kilter.protocol.buffer import SimpleBuffer
from kilter.protocol.core import FilterProtocol
from kilter.protocol.messages import ProtocolFlags

from .session import *
from .util import Broadcast

MessageChannel = anyio.abc.ObjectStream[Message]
Sender = AsyncGenerator[None, Message]

kiB = 2**10
MiB = 2**20

_VALID_FINAL_RESPONSES = Reject, Discard, Accept, TemporaryFailure, ReplyCode
_VALID_EVENT_MESSAGE = Helo, EnvelopeFrom, EnvelopeRecipient, Data, Unknown, \
	Header, EndOfHeaders, Body, EndOfMessage


class NegotiationError(Exception):
	"""
	An error raised when MTAs are not compatible with the filter
	"""


class _Broadcast(Broadcast[EventMessage]):

	def __init__(self) -> None:
		super().__init__()
		self._ready = anyio.Condition()

	async def aclose(self) -> None:
		async with self._ready:
			self._ready.notify_all()

	async def pre_receive_hook(self) -> None:
		async with self._ready:
			self._ready.notify_all()

	async def post_send_hook(self) -> None:
		# Await notification of either a receiver waiting or the broadcaster closing
		# This is necessary to delay returning until a filter has had a chance to return
		# a result.
		async with self._ready:
			await self._ready.wait()


class Runner:
	"""
	A filter runner that coordinates passing data between a stream and multiple filters

	Instances can be used as handlers that can be passed to `anyio.abc.Listener.serve()` or
	used with any `anyio.abc.ByteStream`.
	"""

	def __init__(self, *filters: Filter):
		if len(filters) == 0:  # pragma: no-cover
			raise TypeError("Runner requires at least one filter to run")
		self.filters = filters
		self.use_skip = True

	async def __call__(self, client: anyio.abc.ByteStream) -> None:
		"""
		Return an awaitable that starts and coordinates filters
		"""
		buff = SimpleBuffer(1*MiB)
		proto = FilterProtocol()
		sender = _sender(client, proto)
		channels = list[MessageChannel]()
		macro: Macro|None = None

		await sender.asend(None)  # type: ignore # initialise

		async with anyio.create_task_group() as tasks, aclosing(sender), aclosing(client):
			while 1:
				try:
					buff[:] = await client.receive(buff.available)
				except (anyio.EndOfStream, anyio.ClosedResourceError):
					for channel in channels:
						await channel.aclose()
					return
				for message in proto.read_from(buff):
					match message:
						case Negotiate():
							await self._negotiate(message, sender)
						case Macro() as macro:
							# Note that this Macro will hang around as "macro"; this is for
							# Connect messages.
							for channel in channels:
								await channel.send(macro)
						case Connect():
							channels[:] = await self._connect(message, sender, tasks, macro)
						case Abort():
							for channel in channels:
								await channel.aclose()
						case Close():
							return
						case _:
							assert isinstance(message, _VALID_EVENT_MESSAGE)
							skip = isinstance(message, Body)
							for channel in channels:
								await channel.send(message)
								match (await channel.receive()):
									case Skip():
										continue
									case Continue():
										skip = False
									case Accept():
										await channel.aclose()
										channels.remove(channel)
									case resp:
										await sender.asend(resp)
										break
							else:
								await sender.asend(
									Accept() if len(channels) == 0 else
									Skip() if skip else
									Continue(),
								)

	async def _negotiate(self, message: Negotiate, sender: Sender) -> None:
		# TODO: actually negotiate what the filter wants, not just "everything"
		actions = set(ActionFlags)  # All actions!
		if actions != ActionFlags.unpack(message.action_flags):
			raise NegotiationError("MTA does not accept all actions required by the filter")

		resp = Negotiate(6, 0, 0)
		resp.protocol_flags = message.protocol_flags
		resp.action_flags = ActionFlags.pack(actions)

		await sender.asend(resp)

		self.use_skip = bool(resp.protocol_flags & ProtocolFlags.SKIP)

	async def _connect(
		self,
		message: Connect,
		sender: Sender,
		tasks: anyio.abc.TaskGroup,
		macro: Macro|None,
	) -> list[MessageChannel]:
		channels = list[MessageChannel]()
		for fltr in self.filters:
			lchannel, rchannel = _make_message_channel()
			channels.append(lchannel)
			session = Session(message, sender, _Broadcast())
			if macro:
				await session.deliver(macro)
			match await tasks.start(
				_runner, fltr, session, rchannel, self.use_skip,
			):
				case Continue():
					continue
				case Message() as resp:
					await sender.asend(resp)
					return []
				case _ as arg:  # pragma: no-cover
					raise TypeError(
						f"task_status.started called with bad type: "
						f"{arg!r}",
					)
		await sender.asend(Continue())
		return channels


def _make_message_channel() -> tuple[MessageChannel, MessageChannel]:
	lsend, rrecv = anyio.create_memory_object_stream(1, Message)  # type: ignore
	rsend, lrecv = anyio.create_memory_object_stream(1, Message)  # type: ignore
	return StapledObjectStream(lsend, lrecv), StapledObjectStream(rsend, rrecv)


async def _sender(client: anyio.abc.ByteSendStream, proto: FilterProtocol) -> Sender:
	buff = SimpleBuffer(1*kiB)
	while 1:
		proto.write_to(buff, (yield))
		await client.send(buff[:])
		del buff[:]


async def _runner(
	fltr: Filter,
	session: Session,
	channel: MessageChannel,
	use_skip: bool, *,
	task_status: anyio.abc.TaskStatus,
) -> None:
	final_resp: ResponseMessage|None = None

	async def _filter_wrap(
		task_status: anyio.abc.TaskStatus,
	) -> None:
		nonlocal final_resp
		async with session:
			task_status.started()
			final_resp = await fltr(session)
		if not isinstance(final_resp, _VALID_FINAL_RESPONSES):
			warn(f"expected a final response from {fltr}, got {final_resp}")
			final_resp = TemporaryFailure()

	async with anyio.create_task_group() as tasks:
		await tasks.start(_filter_wrap)
		task_status.started(final_resp or Continue())
		while final_resp is None:
			try:
				message = await channel.receive()
			except (anyio.EndOfStream, anyio.ClosedResourceError):
				tasks.cancel_scope.cancel()
				return
			if isinstance(message, Macro):
				await session.deliver(message)
				continue
			assert isinstance(message, _VALID_EVENT_MESSAGE)
			resp = await session.deliver(message)
			if final_resp is not None:
				await channel.send(final_resp)  # type: ignore
			elif use_skip and resp == Skip:
				await channel.send(Skip())
			else:
				await channel.send(Continue())
