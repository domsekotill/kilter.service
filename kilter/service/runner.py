# Copyright 2022-2023, 2025 Dominik Sekotill <dom.sekotill@kodo.org.uk>
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

import enum
import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Final
from typing import TypeAlias
from warnings import warn

import anyio.abc
from async_generator import aclosing

from kilter.protocol.buffer import SimpleBuffer
from kilter.protocol.core import EventMessage
from kilter.protocol.core import FilterMessage
from kilter.protocol.core import FilterProtocol
from kilter.protocol.core import ResponseMessage
from kilter.protocol.messages import *

from .options import get_flags
from .options import get_macros
from .session import Aborted
from .session import Filter
from .session import FilterResponse
from .session import Session
from .util import Broadcast
from .util import qualname

__all__ = [
	"Runner",
	"NegotiationError",
]

FinalResponse: TypeAlias = FilterResponse | TemporaryFailure

kiB: Final = 2**10
MiB: Final = 2**20

_logger = logging.getLogger(__package__)


class NegotiationError(Exception):
	"""
	An error raised when MTAs are not compatible with the filter
	"""


class State(enum.Enum):

	CONNECTED = enum.auto()
	SESSION = enum.auto()
	SESSION_ABORTED = enum.auto()
	MESSAGE = enum.auto()
	MESSAGE_ABORTED = enum.auto()


class _Broadcast(Broadcast[EventMessage]):

	def __init__(self) -> None:
		super().__init__()
		self.task_status = list[anyio.abc.TaskStatus[None]]()

	async def shutdown_hook(self) -> None:
		await self.pre_receive_hook()

	async def pre_receive_hook(self) -> None:
		while self.task_status:
			self.task_status.pop().started()


class Sender:
	"""
	Concrete implementation of `kilter.service.session.Sender`
	"""

	def __init__(self, client: anyio.abc.ByteSendStream, proto: FilterProtocol) -> None:
		self.client = client
		self.proto = proto

	async def send(self, message: FilterMessage) -> None:
		"""
		Encode and send a message to the client stream
		"""
		buffer = SimpleBuffer(1*kiB)
		self.proto.write_to(buffer, message)
		await self.client.send(buffer[:])
		if __debug__:
			_logger.debug(f"sent: {message}")


class Runner:
	"""
	A filter runner that coordinates passing data between a stream and multiple filters

	Instances can be used as handlers that can be passed to `anyio.abc.Listener.serve()` or
	used with any `anyio.abc.ByteStream`.
	"""

	def __init__(self, *filters: Filter):
		if len(filters) == 0:  # pragma: no-cover
			raise TypeError("Runner requires at least one filter to run")
		self.filters = set(filters)
		if len(filters) != len(self.filters):
			warn("Repeated filters will only be run once", stacklevel=2)
		self.use_skip = True

	async def __call__(self, client: anyio.abc.ByteStream) -> None:
		"""
		Return an awaitable that starts and coordinates filters
		"""
		buff = SimpleBuffer(1*MiB)
		proto = FilterProtocol(abort_on_unknown=True)
		sender = Sender(client, proto)
		session = Session(sender, _Broadcast())
		runner = SessionRunner(session)
		state = State.CONNECTED

		async with (
			aclosing(client),
			anyio.create_task_group() as tasks,
		):
			while 1:
				try:
					buff[:] = await client.receive(buff.available)
				except (
					anyio.EndOfStream,
					anyio.ClosedResourceError,
					anyio.BrokenResourceError,
				):
					return
				for message in proto.read_from(buff):
					if __debug__:
						_logger.debug(f"received: {message}")

					# If previous message was Abort, restart filters for any non-Abort/Close
					# message
					if state in (State.SESSION_ABORTED, State.MESSAGE_ABORTED):
						if not isinstance(message, Abort|Close):
							await runner.start(self.filters, tasks)
						state = (
							State.CONNECTED if state == State.SESSION_ABORTED else
							State.SESSION
						)

					match message:
						case Negotiate():
							await sender.send(await self._negotiate(message))
							continue
						case Connect():
							_logger.info(f"Client connected from {message.hostname}")
							await session.deliver(message)
							await runner.start(self.filters, tasks)
							if proto.needs_response(message):
								await sender.send(await runner.check_response() or Continue())
							continue
						case Helo():
							state = State.SESSION
						case EnvelopeFrom():
							state = State.MESSAGE
						case Abort() if state in (State.SESSION, State.MESSAGE):
							state = (
								State.SESSION_ABORTED if state == State.SESSION else
								State.MESSAGE_ABORTED
							)
						case Abort():
							_logger.warning("Unexpected Abort received")
							state = State.CONNECTED
						case Close():
							tasks.cancel_scope.cancel()
							return

					skip_or_cont = await session.deliver(message)
					if not proto.needs_response(message):
						continue
					if (resp := await runner.check_response()):
						await sender.send(resp)
					elif self.use_skip:
						await sender.send(skip_or_cont())
					else:
						await sender.send(Continue())

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


class SessionRunner:

	def __init__(self, session: Session):
		self.session = session
		self.filters = dict[Filter, FinalResponse|None]()

	async def start(self, filters: Iterable[Filter], task_group: anyio.abc.TaskGroup) -> None:
		"""
		Run all the given filters in a task group

		The session MUST have been primed by the delivery of a Connect message beforehand or
		filters will be unable to access the connection details.
		"""
		_logger.debug("Starting filters")
		for flter in filters:
			await task_group.start(self.run_filter, flter)

	async def run_filter(
		self,
		flter: Filter,
		task_status: anyio.abc.TaskStatus[None],
	) -> None:
		"""
		Run a filter as a subtask in a task group

		A `Future` for returning the filter's response is added to the
		`SessionRunner.filter` dict.
		"""
		if flter in self.filters:
			raise RuntimeError
		self.filters[flter] = None

		async with self.session:
			assert isinstance(self.session.broadcast, _Broadcast)
			status_notifiers = self.session.broadcast.task_status
			status_notifiers.append(task_status)

			try:
				resp: FinalResponse = await flter(self.session)
			except Aborted:
				_logger.debug(f"Aborted filter {qualname(flter)}")
				del self.filters[flter]
				return
			except Exception:
				_logger.exception(f"Error in filter {qualname(flter)}")
				resp = TemporaryFailure()
			if not isinstance(resp, FinalResponse):
				warn(f"expected a valid response from {qualname(flter)}, got {resp}")  # type: ignore # Don't fully trust users…
				resp = TemporaryFailure()
			self.filters[flter] = resp
			if task_status in status_notifiers:
				status_notifiers.remove(task_status)
				task_status.started()

	async def check_response(self) -> ResponseMessage|None:
		assert self.filters, "no filters when checking for a response"
		response: ResponseMessage|None = None
		complete = list[Filter]()
		for flter, result in self.filters.items():
			# If a filter has not finished or no response is expected, continue without
			# removing from filter container; remove failed filters and filters that have
			# accepted; return a response for rejections;
			match result:
				case None:
					continue
				case Accept():
					_logger.info("Accept from %s, waiting for remaining", qualname(flter))
				case TemporaryFailure() as response:
					_logger.warning("Filter failed: %s", flter)
				case Reject()|Discard()|ReplyCode() as response:
					_logger.info("Returning response %s from %s", type(response).__name__, qualname(flter))
					complete[:] = self.filters
					break
				case msg:
					raise AssertionError(f"unexpected filter result: {msg}")
			complete.append(flter)
		for flter in complete:
			del self.filters[flter]
		return response if response else None if self.filters else Accept()
