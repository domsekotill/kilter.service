# Copyright 2022 Dominik Sekotill <dom.sekotill@kodo.org.uk>
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Sessions are the kernel of a filter, providing it with an async API to access messages
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import TYPE_CHECKING
from typing import AsyncContextManager
from typing import Literal
from typing import Protocol
from typing import TypeVar
from typing import Union

from ..protocol.messages import *
from . import util

EventMessage = Union[
	Connect, Helo, EnvelopeFrom, EnvelopeRecipient, Data, Unknown,
	Header, EndOfHeaders, Body, EndOfMessage,
]
ResponseMessage = Union[
	Continue, Reject, Discard, Accept, TemporaryFailure, Skip,
	ReplyCode, Abort,
]
EditMessage = Union[
	AddHeader, ChangeHeader, InsertHeader, ChangeSender, AddRecipient, AddRecipientPar,
	RemoveRecipient, ReplaceBody,
]


class Filter(Protocol):
	"""
	Filters are callables that accept a `Session` and return a response
	"""

	async def __call__(self, session: Session) -> ResponseMessage: ...  # noqa: D102


class Phase(int, Enum):
	"""
	Session phases indicate what messages to expect and are impacted by received messages

	Users should not generally need to use these values, however an understanding of the
	state-flow they represent is useful for understanding some error exception
	raised by `Session` methods.

	CONNECT:
	  This phase is the starting phase of a session, during which a HELO/EHLO message may be
	  awaited with `Session.helo()`.

	MAIL:
	  This phase is entered after HELO/EHLO, during which a MAIL message may be awaited with
	  `Session.envelope_from()`.  The `Session.extension()` method may also be used to get
	  the raw MAIL command with any extension arguments, or any other extension commands
	  that the MTA does not support (if the MTA supports passing these commands to
	  a filter).

	ENVELOPE:
	  This phase is entered after MAIL, during which any RCPT commands may be awaited with
	  `Session.envelope_recipients()`.  The `Session.extension()` method may also be used to
	  get the raw RCPT command with any extension arguments, or any other extension commands
	  that the MTA does not support (if the MTA supports passing these commands to
	  a filter).

	HEADERS:
	  This phase is entered after a DATA command, while message headers are processed.
	  Headers may be iterated as they arrive, or be collected for later through the
	  `Session.headers` object.

	BODY:
	  This phase is entered after a message's headers have been processed.  The raw message
	  body may be iterated over in chunks through the `Session.body` object.

	POST:
	  This phase is entered once a message's body has been completed (or skipped).  During
	  this phase the message editing methods of a `Session` object or the `Session.headers`
	  and `Session.body` objects may be used.
	"""

	CONNECT = 1
	MAIL = 2
	ENVELOPE = 3
	HEADERS = 4
	BODY = 5
	POST = 6


@dataclass
class Position:
	"""
	A base class for `Before` and `After`, this class is not intended to be used directly
	"""

	subject: Header|Literal["start"]|Literal["end"]


class Before(Position):
	"""
	Indicates a relative position preceding a subject `Header` in a header list

	See `HeadersAccessor.insert`.
	"""

	subject: Header


class After(Position):
	"""
	Indicates a relative position following a subject `Header` in a header list

	See `HeadersAccessor.insert`.
	"""

	subject: Header


START = Position("start")
END = Position("end")


class Session:
	"""
	The kernel of a filter, providing an API for filters to access messages from and MTA
	"""

	def __init__(
		self,
		connmsg: Connect,
		sender: AsyncGenerator[None, EditMessage],
	):
		self.host = connmsg.hostname
		self.address = connmsg.address
		self.port = connmsg.port

		self._editor = sender
		self._broadcast = util.Broadcast[EventMessage]()

		self.headers = HeadersAccessor(self, sender)
		self.body = BodyAccessor(self, sender)

		self.skip = False

		# Phase checking is a bit fuzzy as a filter may not request every message,
		# so some phases will be skipped; checks should not try to exactly match a phase.
		self.phase = Phase.CONNECT

	async def deliver(self, message: EventMessage) -> type[Continue]|type[Skip]:
		"""
		Deliver a message (or its contents) to a task waiting for it
		"""
		match message:
			case Body() if self.skip:
				return Skip
			case Helo():
				self.phase = Phase.MAIL
			case EnvelopeFrom() | EnvelopeRecipient() | Unknown():
				self.phase = Phase.ENVELOPE
			case Data() | Header():
				self.phase = Phase.HEADERS
			case EndOfHeaders() | Body():
				self.phase = Phase.BODY
			case EndOfMessage():  # pragma: no-branch
				self.phase = Phase.POST
		await self._broadcast.send(message)
		return Skip if self.phase == Phase.BODY and self.skip else Continue

	async def helo(self) -> str:
		"""
		Wait for a HELO/EHLO message and return the client's claimed hostname
		"""
		if self.phase > Phase.CONNECT:
			raise RuntimeError(
				"Session.helo() must be awaited before any other async features of a "
				"Session",
			)
		async with self._broadcast:
			while self.phase <= Phase.CONNECT:
				message = await self._broadcast.receive()
				if isinstance(message, Helo):
					return message.hostname
		raise RuntimeError("HELO/EHLO event not received")

	async def envelope_from(self) -> str:
		"""
		Wait for a MAIL command message and return the sender identity

		Note that if extensions arguments are wanted, users should use `Session.extension()`
		instead with a name of `MAIL`.
		"""
		if self.phase > Phase.MAIL:
			raise RuntimeError(
				"Session.envelope_from() may only be awaited before the ENVELOPE phase",
			)
		async with self._broadcast:
			while self.phase <= Phase.MAIL:
				message = await self._broadcast.receive()
				if isinstance(message, EnvelopeFrom):
					return message.sender.decode()
		raise RuntimeError("MAIL event not received")

	async def envelope_recipients(self) -> AsyncIterator[str]:
		"""
		Wait for RCPT command messages and iteratively yield the recipients' identities

		Note that if extensions arguments are wanted, users should use `Session.extension()`
		instead with a name of `RCPT`.
		"""
		if self.phase > Phase.ENVELOPE:
			raise RuntimeError(
				"Session.envelope_from() may only be awaited before the HEADERS phase",
			)
		async with self._broadcast:
			while self.phase <= Phase.ENVELOPE:
				message = await self._broadcast.receive()
				if isinstance(message, EnvelopeRecipient):
					yield message.recipient.decode()

	async def extension(self, name: str) -> memoryview:
		"""
		Wait for the named command extension and return the raw command for processing
		"""
		if self.phase > Phase.ENVELOPE:
			raise RuntimeError(
				"Session.extension() may only be awaited before the HEADERS phase",
			)
		async with self._broadcast:
			while self.phase <= Phase.ENVELOPE:
				message = await self._broadcast.receive()
				match message:
					case Unknown():
						bname = name.encode("utf-8")
						if message.content[:len(bname)] == bname:
							return message.content
					# fake buffers for MAIL and RCPT commands
					case EnvelopeFrom() if name == "MAIL":
						vals = [b"MAIL FROM", message.sender, *message.arguments]
						return memoryview(b" ".join(vals))
					case EnvelopeRecipient() if name == "RCPT":
						vals = [b"RCPT TO", message.recipient, *message.arguments]
						return memoryview(b" ".join(vals))
		raise RuntimeError(f"{name} event not received")

	async def change_sender(self, sender: str, args: str = "") -> None:
		"""
		Move onto the `Phase.POST` phase and instruct the MTA to change the sender address
		"""
		await _until_editable(self)
		await self._editor.asend(ChangeSender(sender, args or None))

	async def add_recipient(self, recipient: str, args: str = "") -> None:
		"""
		Move onto the `Phase.POST` phase and instruct the MTA to add a new recipient address
		"""
		await _until_editable(self)
		await self._editor.asend(
			AddRecipientPar(recipient, args) if args else AddRecipient(recipient),
		)

	async def remove_recipient(self, recipient: str) -> None:
		"""
		Move onto the `Phase.POST` phase and instruct the MTA to remove a recipient address
		"""
		await _until_editable(self)
		await self._editor.asend(RemoveRecipient(recipient))


class HeadersAccessor(AsyncContextManager["HeaderIterator"]):
	"""
	A class that allows access and modification of the message headers sent from an MTA

	To access headers (which are only available iteratively), use an instance as an
	asynchronous context manager; a `HeaderIterator` is returned when the context is
	entered.
	"""

	def __init__(self, session: Session, sender: AsyncGenerator[None, EditMessage]):
		self.session = session
		self._editor = sender
		self._table = list[Header]()
		self._aiter: AsyncGenerator[Header, None]

	async def __aenter__(self) -> HeaderIterator:
		self._aiter = HeaderIterator(self.__aiter())
		return self._aiter

	async def __aexit__(self, *_: object) -> None:
		await self._aiter.aclose()

	async def __aiter(self) -> AsyncGenerator[Header, None]:
		async with self.session._broadcast:
			# yield from cached headers first; allows multiple tasks to access the headers
			# in an uncoordinated manner; note the broadcaster is locked at this point
			for header in self._table:
				yield header
			while self.session.phase <= Phase.HEADERS:
				match (await self.session._broadcast.receive()):
					case Header() as header:
						self._table.append(header)
						try:
							yield header
						except GeneratorExit:
							await self._collect()
							raise
					case EndOfHeaders():
						return

	async def collect(self) -> None:
		"""
		Collect all headers without producing an iterator

		Calling this method before the `Phase.BODY` phase allows later processing of headers
		(after the HEADER phase) without the need for an empty loop.
		"""
		async with self.session._broadcast:
			await self._collect()

	async def _collect(self) -> None:
		# note the similarities between this and __aiter; the difference is no mutex or
		# yields
		while self.session.phase <= Phase.HEADERS:
			match (await self.session._broadcast.receive()):
				case Header() as header:
					self._table.append(header)
				case EndOfHeaders():
					return

	async def delete(self, header: Header) -> None:
		"""
		Move onto the `Phase.POST` phase and Instruct the MTA to delete the given header
		"""
		await self.collect()
		await _until_editable(self.session)
		index = self._table.index(header)
		await self._editor.asend(ChangeHeader(index, header.name, b""))
		del self._table[index]

	async def update(self, header: Header, value: bytes) -> None:
		"""
		Move onto the `Phase.POST` phase and Instruct the MTA to modify the value of a header
		"""
		await self.collect()
		await _until_editable(self.session)
		index = self._table.index(header)
		await self._editor.asend(ChangeHeader(index, header.name, value))
		self._table[index].value = value

	async def insert(self, header: Header, position: Position) -> None:
		"""
		Move onto the `Phase.POST` phase and instruct the MTA to insert a new header

		The header is inserted at `START`, `END`, or a relative position with `Before` and
		`After`; for example `Before(Header("To", "test@example.com"))`.
		"""
		await self.collect()
		await _until_editable(self.session)
		match position:
			case Position(subject="start"):
				index = 0
			case Position(subject="end"):
				index = len(self._table)
			case Before():
				index = self._table.index(position.subject)
			case After():  # pragma: no-branch
				index = self._table.index(position.subject) + 1
		if index >= len(self._table):
			await self._editor.asend(AddHeader(header.name, header.value))
			self._table.append(header)
		else:
			await self._editor.asend(InsertHeader(index, header.name, header.value))
			self._table.insert(index, header)


class HeaderIterator(AsyncGenerator[Header, None]):
	"""
	Iterator for headers obtained by using a `HeaderAccessor` as a context manager
	"""

	if TYPE_CHECKING:
		Self = TypeVar("Self", bound="HeaderIterator")

	def __init__(self, aiter: AsyncGenerator[Header, None]):
		self._aiter = aiter

	def __aiter__(self: Self) -> Self:
		return self

	async def __anext__(self) -> Header:  # noqa: D102
		return await self._aiter.__anext__()

	async def asend(self, value: None = None) -> Header:  # noqa: D102
		return await self._aiter.__anext__()

	async def athrow(  # noqa: D102
		self,
		e: type[BaseException]|BaseException,
		m: object = None,
		t: TracebackType|None = None, /,
	) -> Header:
		if isinstance(e, type):
			return await self._aiter.athrow(e, m, t)
		assert m is None
		return await self._aiter.athrow(e, m, t)

	async def aclose(self) -> None:  # noqa: D102
		await self._aiter.aclose()

	async def restrict(self, *names: str) -> AsyncIterator[Header]:
		"""
		Return an asynchronous generator that filters headers by name
		"""
		async for header in self._aiter:
			if header.name in names:
				yield header


class BodyAccessor(AsyncContextManager[AsyncIterator[memoryview]]):
	"""
	A class that allows access and modification of the message body sent from an MTA

	To access chunks of abody (which are only available iteratively), use an instance as an
	asynchronous context manager; a `BodyIterator` is returned when the context is
	entered.
	"""

	def __init__(self, session: Session, sender: AsyncGenerator[None, EditMessage]):
		self.session = session
		self._editor = sender

	async def __aenter__(self) -> AsyncIterator[memoryview]:
		self._aiter = self.__aiter()
		return self._aiter

	async def __aexit__(self, *_: object) -> None:
		await self._aiter.aclose()

	async def __aiter(self) -> AsyncGenerator[memoryview, None]:
		async with self.session._broadcast:
			while self.session.phase <= Phase.BODY:
				match (await self.session._broadcast.receive()):
					case Body() as body:
						try:
							yield body.content
						except GeneratorExit:
							self.session.skip = True
							raise
					case EndOfMessage() as eom:
						if not self.session.skip:
							yield eom.content

	async def write(self, chunk: bytes) -> None:
		"""
		Request that chunks of a new message body are sent to the MTA
		"""
		await _until_editable(self.session)
		await self._editor.asend(ReplaceBody(chunk))


async def _until_editable(session: Session) -> None:
	if session.phase == Phase.POST:
		return
	async with session._broadcast:
		while session.phase < Phase.POST:
			await session._broadcast.receive()
