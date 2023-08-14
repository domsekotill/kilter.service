# Copyright 2022-2023 Dominik Sekotill <dom.sekotill@kodo.org.uk>
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

import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Final
from typing import TypeAlias
from warnings import warn

import anyio.abc
from anyio.streams.stapled import StapledObjectStream
from async_generator import aclosing
from typing_extensions import Self

from kilter.protocol.buffer import SimpleBuffer
from kilter.protocol.core import EditMessage
from kilter.protocol.core import EventMessage
from kilter.protocol.core import FilterProtocol
from kilter.protocol.core import ResponseMessage
from kilter.protocol.messages import ProtocolFlags

from .options import get_flags
from .options import get_macros
from .session import *
from .util import Broadcast
from .util import qualname

MessageChannel: TypeAlias = anyio.abc.ObjectStream[Message]
Sender: TypeAlias = AsyncGenerator[None, ResponseMessage|EditMessage|Negotiate|Skip]

kiB: Final = 2**10
MiB: Final = 2**20

# TODO: Convert to Union type alias once python/mypy#14242 is fixed
_VALID_FINAL_RESPONSES: Final = Reject, Discard, Accept, TemporaryFailure, ReplyCode
_VALID_EVENT_MESSAGE: TypeAlias = Helo | EnvelopeFrom | EnvelopeRecipient | Data | \
	Unknown | Header | EndOfHeaders | Body | EndOfMessage | Abort

_logger = logging.getLogger(__package__)


class NegotiationError(Exception):
	"""
	An error raised when MTAs are not compatible with the filter
	"""


class _CloseFilter:

	def __init__(self, filtr: Filter):
		self.filter = filtr


class _Broadcast(Broadcast[EventMessage]):

	def __init__(self) -> None:
		super().__init__()
		self.task_status: anyio.abc.TaskStatus[None]|None = None

	async def shutdown_hook(self) -> None:
		await self.pre_receive_hook()

	async def pre_receive_hook(self) -> None:
		if self.task_status is not None:
			self.task_status.started()
			self.task_status = None


class Runner:
	"""
	A filter runner that coordinates passing data between a stream and multiple filters

	Instances can be used as handlers that can be passed to `anyio.abc.Listener.serve()` or
	used with any `anyio.abc.ByteStream`.
	"""

	def __init__(self, *filters: Filter):
		if len(filters) == 0:  # pragma: no-cover
			raise TypeError("Runner requires at least one filter to run")
		self.filters = list(filters)
		self.use_skip = True

	async def __call__(self, client: anyio.abc.ByteStream) -> None:
		"""
		Return an awaitable that starts and coordinates filters
		"""
		buff = SimpleBuffer(1*MiB)
		proto = FilterProtocol(abort_on_unknown=True)
		sender = _sender(client, proto)
		macro: Macro|None = None
		aborted = False

		await sender.asend(None)  # type: ignore # initialise

		async with (
			anyio.create_task_group() as tasks,
			aclosing(sender), aclosing(client),
			_TaskRunner(tasks) as runner,
		):
			while 1:
				try:
					buff[:] = await client.receive(buff.available)
				except (
					anyio.EndOfStream,
					anyio.ClosedResourceError,
					anyio.BrokenResourceError,
				):
					await runner.aclose()
					return
				for message in proto.read_from(buff):
					if __debug__:
						_logger.debug(f"received: {message}")
					match message:
						case Negotiate():
							await sender.asend(await self._negotiate(message))
						case Macro() as macro:
							# Note that this Macro will hang around as "macro"; this is for
							# Connect messages.
							await runner.set_macros(macro)
						case Connect():
							await self._prepare_filters(message, sender, runner)
							if macro:
								await runner.set_macros(macro)
							needs_response = proto.needs_response(message)
							match await runner.start(needs_response, True, self.use_skip):
								case None:
									assert not needs_response
								case _CloseFilter() as notif:
									self.filters.remove(notif.filter)
								case c_resp if needs_response:
									assert c_resp is not None and not isinstance(c_resp, _CloseFilter)
									await sender.asend(c_resp)
								case c_resp:
									raise RuntimeError(f"unexpected response: {c_resp}")
						case Abort():
							aborted = True
							await runner.abort(message)
						case Close():
							await runner.aclose()
							return
						case _:
							if aborted:
								aborted = False
								await runner.start(True, False, self.use_skip)
							needs_response = proto.needs_response(message)
							match await runner.message_events(message, needs_response):
								case None:
									assert not needs_response
								case _CloseFilter() as notif:
									self.filters.remove(notif.filter)
								case resp if needs_response:
									assert resp is not None and not isinstance(resp, _CloseFilter)
									await sender.asend(resp)
								case resp:
									raise RuntimeError(f"unexpected response: {resp}")

	async def _negotiate(self, message: Negotiate) -> Negotiate:
		_logger.info("Negotiating with MTA")

		optmask = ProtocolFlags.NONE
		options = \
			ProtocolFlags.SKIP | \
			ProtocolFlags.NO_HELO | \
			ProtocolFlags.NO_SENDER | ProtocolFlags.NO_RECIPIENT | \
			ProtocolFlags.NO_DATA | ProtocolFlags.NO_BODY | \
			ProtocolFlags.NO_HEADERS | ProtocolFlags.NO_END_OF_HEADERS | \
			ProtocolFlags.NR_CONNECT | ProtocolFlags.NR_HELO | \
			ProtocolFlags.NR_SENDER | ProtocolFlags.NR_RECIPIENT | \
			ProtocolFlags.NR_DATA | ProtocolFlags.NR_BODY | \
			ProtocolFlags.NR_HEADER | ProtocolFlags.NR_END_OF_HEADERS
		actions = ActionFlags.NONE
		macros = defaultdict(set)

		options &= message.protocol_flags  # Remove unoffered initial flags, they are not required

		for filtr in self.filters:
			flags = get_flags(filtr)
			optmask |= flags.unset_options
			options |= flags.set_options
			actions |= flags.set_actions

			for stage, names in get_macros(filtr).items():
				macros[stage].update(names)

		options &= ~optmask

		if (missing_actions := actions & ~message.action_flags):
			raise NegotiationError(f"MTA does not accept {missing_actions}")

		if (missing_options := options & ~message.protocol_flags):
			raise NegotiationError(f"MTA does not offer {missing_options}")

		self.use_skip = ProtocolFlags.SKIP in options

		return Negotiate(6, actions, options, dict(macros))

	async def _prepare_filters(
		self,
		message: Connect,
		sender: Sender,
		runner: _TaskRunner,
	) -> None:
		_logger.info(f"Client connected from {message.hostname}")
		for fltr in self.filters:
			session = Session(message, sender, _Broadcast())
			runner.add_filter(fltr, session)


class _TaskRunner:

	def __init__(self, tasks: anyio.abc.TaskGroup):
		self.tasks = tasks
		self.filters = list[tuple[Filter, Session]]()
		self.channels = dict[MessageChannel, Filter]()

	async def __aenter__(self) -> Self:
		return self

	async def __aexit__(self, *_: object) -> None:
		await self.aclose()

	def add_filter(self, flter: Filter, session: Session, /) -> None:
		self.filters.append((flter, session))

	async def start(
		self,
		needs_response: bool,
		first_connect: bool,
		use_skip: bool,
	) -> ResponseMessage|_CloseFilter|None:
		if self.channels:
			raise RuntimeError(f"{self} is already running tasks")
		final: ResponseMessage = Accept()
		for flter, session in self.filters:
			lchannel, rchannel = _make_message_channel()
			self.channels[lchannel] = flter
			match await self.tasks.start(self._runner, flter, session, rchannel, use_skip):
				case Accept():
					del self.channels[lchannel]
				case Continue():
					continue
				case TemporaryFailure() as final:  # replaces final
					pass
				case Reject()|Discard()|ReplyCode() as resp:
					if not first_connect:
						_logger.warning(
							f"Ignoring unexpected response from filter after restart: "
							f"{qualname(flter)} -> {resp}",
						)
						continue
					if not needs_response:
						_logger.warning(
							f"Unexpected response from filter {qualname(flter)}",
						)
						return _CloseFilter(flter)
					return resp
				case _ as arg:  # pragma: no-cover
					raise TypeError(f"task_status.started called with bad type: {arg!r}")
		if not needs_response:
			return None
		return final if len(self.channels) == 0 else Continue()

	async def set_macros(self, message: Macro) -> None:
		if self.channels:
			for channel in self.channels:
				await channel.send(message)
		else:
			for _, session in self.filters:
				await session.deliver(message)

	async def message_events(
		self,
		message: _VALID_EVENT_MESSAGE,
		needs_response: bool,
	) -> ResponseMessage|Skip|_CloseFilter|None:
		skip = isinstance(message, Body)
		for channel in list(self.channels):
			await channel.send(message)
			match (await channel.receive()):
				case Skip():
					continue
				case Continue():
					skip = False
				case Accept() as resp:
					flter = await self.close_channel(channel)
					if len(self.channels) == 0:
						_logger.info(f"Returning response Accept from {qualname(flter)}")
						return resp
					_logger.info(f"Holding response Accept from {qualname(flter)}")
				case (Reject() | Discard() | TemporaryFailure() | ReplyCode()) as resp:
					flter = await self.close_channel(channel)
					if not needs_response:
						_logger.warning(f"Unexpected response from filter {qualname(flter)}")
						return _CloseFilter(flter)
					_logger.info(f"Returning response {type(resp).__name__} from {qualname(flter)}")
					return resp
		assert len(self.channels) > 0, "Running filters reached zero without a response?!"
		if not needs_response:
			return None
		return Skip() if skip else Continue()

	async def close_channel(self, channel: MessageChannel) -> Filter:
		await channel.aclose()
		return self.channels.pop(channel)

	async def abort(self, abort: Abort) -> None:
		if not self.channels:
			return
		_logger.info("Aborting filters")
		for channel in self.channels:
			await channel.send(abort)
			await channel.receive()
			await channel.aclose()
		self.channels.clear()

	async def aclose(self) -> None:
		if self.channels:
			_logger.info("Closing filters")
		self.tasks.cancel_scope.cancel()
		self.channels.clear()

	@staticmethod
	async def _runner(
		fltr: Filter,
		session: Session,
		channel: MessageChannel,
		use_skip: bool, *,
		task_status: anyio.abc.TaskStatus[ResponseMessage],
	) -> None:
		final_resp: ResponseMessage|None = None

		async def _filter_wrap(
			task_status: anyio.abc.TaskStatus[None],
		) -> None:
			nonlocal final_resp
			async with session:
				assert isinstance(session.broadcast, _Broadcast)
				session.broadcast.task_status = task_status
				try:
					final_resp = await fltr(session)
				except Aborted:
					_logger.debug(f"Aborted filter {qualname(fltr)}")
					return
				except Exception:
					_logger.exception(f"Error in filter {qualname(fltr)}")
					final_resp = TemporaryFailure()
				if not isinstance(final_resp, _VALID_FINAL_RESPONSES):
					warn(f"expected a valid response from {qualname(fltr)}, got {final_resp}")
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
				# TODO: Upgrade and remove ignores once python/mypy#14242 is in
				assert isinstance(message, _VALID_EVENT_MESSAGE)  # type: ignore[misc,arg-type]
				resp = await session.deliver(message)  # type: ignore[arg-type]
				if isinstance(message, Abort):
					await channel.send(Continue())
					await channel.aclose()
					return
				if final_resp is not None:
					break  # type: ignore[unreachable]
				await channel.send(Skip() if use_skip and resp == Skip else Continue())
			await channel.send(final_resp)


def _make_message_channel() -> tuple[MessageChannel, MessageChannel]:
	lsend, rrecv = anyio.create_memory_object_stream(1, Message)  # type: ignore
	rsend, lrecv = anyio.create_memory_object_stream(1, Message)  # type: ignore
	return StapledObjectStream(lsend, lrecv), StapledObjectStream(rsend, rrecv)


async def _sender(client: anyio.abc.ByteSendStream, proto: FilterProtocol) -> Sender:
	buff = SimpleBuffer(1*kiB)
	while 1:
		proto.write_to(buff, (message := (yield)))
		if __debug__:
			_logger.debug(f"sent: {message}")
		await client.send(buff[:])
		del buff[:]
