from __future__ import annotations

import typing
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from types import TracebackType
from typing import AsyncContextManager
from typing import Literal

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.stapled import StapledByteStream
from anyio.streams.stapled import StapledObjectStream
from async_generator import aclosing
from typing_extensions import Self

from kilter.protocol import *
from kilter.protocol.buffer import SimpleBuffer
from kilter.service import ResponseMessage
from kilter.service import Runner

P = typing.ParamSpec("P")
SendT = typing.TypeVar("SendT")
YieldT = typing.TypeVar("YieldT")

DEFAULT_PEER = "test.example.com"


def _make_aclosing(
	func: Callable[P, AsyncGenerator[YieldT, SendT]],
) -> Callable[P, AsyncContextManager[AsyncGenerator[YieldT, SendT]]]:

	@wraps(func)
	@asynccontextmanager
	async def wrap(*a: P.args, **k: P.kwargs) -> AsyncIterator[AsyncGenerator[YieldT, SendT]]:
		agen = func(*a, **k)
		async with aclosing(agen):
			yield agen

	return wrap


class MockMessageStream:
	"""
	A mock of the right-side of an `anyio.abc.ByteStream` with test support on the left side
	"""

	def __init__(self) -> None:
		self.buffer = SimpleBuffer(1024)
		self.closed = False

	async def __aenter__(self) -> Self:
		send_obj, recv_bytes = anyio.create_memory_object_stream[bytes](5)
		send_bytes, recv_obj = anyio.create_memory_object_stream[bytes](5)

		self._stream = StapledObjectStream(send_obj, recv_obj)
		self.peer_stream = StapledByteStream(
			send_bytes,  # type: ignore
			BufferedByteReceiveStream(recv_bytes),
		)
		await self._stream.__aenter__()
		await self.peer_stream.__aenter__()
		return self

	async def __aexit__(
		self,
		et: type[BaseException]|None = None,
		ex: BaseException|None = None,
		tb: TracebackType|None = None,
	) -> None:
		if not self.closed:
			if et is not None:
				await self.abort()
			else:
				await self.close()
		await self._stream.__aexit__(et, ex, tb)
		await self.peer_stream.__aexit__(et, ex, tb)

	@classmethod
	@asynccontextmanager
	async def started(cls, runner: Runner) -> AsyncIterator[Self]:
		"""
		Return a context manager that yields a prepared stream mock connected to a runner
		"""
		async with anyio.create_task_group() as tg, cls() as stream_mock:
			tg.start_soon(runner, stream_mock.peer_stream)
			await anyio.wait_all_tasks_blocked()
			yield stream_mock

	@classmethod
	@asynccontextmanager
	async def connected(
		cls,
		runner: Runner,
		host: str = DEFAULT_PEER,
		/,
		helo: str|None|Literal[False] = DEFAULT_PEER,
	) -> AsyncIterator[Self]:
		"""
		Return a context manager that yields a prepared and connected stream mock

		Negotiate, Connect, and optionally Helo messages will have been sent over the stream
		once the context has been entered.
		"""
		async with cls.started(runner) as self:
			await self.send_and_expect(make_negotiate(), Negotiate)
			await self.send_and_expect(Connect(host), Continue)
			if helo:
				await self.send_msg(Helo(helo))
			yield self
			if helo:
				await self._abort()
			await self.close()

	@asynccontextmanager
	async def envelope(self, sender: bytes, *recipients: bytes) -> AsyncIterator[None]:
		"""
		Return a context manager that encapsulates a message envelope
		"""
		await self.send_and_expect(EnvelopeFrom(sender), Continue)
		for recipient in recipients:
			await self.send_and_expect(EnvelopeRecipient(recipient), Continue)
		yield
		await self._abort()

	async def abort(self) -> None:
		"""
		Send Abort and close the stream
		"""
		await self._abort()
		await self.close()

	async def _abort(self) -> None:
		try:
			resp = await self.send_msg(Abort())
		except anyio.BrokenResourceError:
			return
		assert len(resp) == 0, resp

	async def close(self) -> None:
		"""
		Send Close and close the stream
		"""
		if self.closed:
			return
		self.closed = True
		try:
			resp = await self.send_msg(Close())
		except anyio.BrokenResourceError:
			return
		assert len(resp) == 0, resp
		await self._stream.aclose()

	async def send_msg(self, msg: Message) -> list[Message]:
		"""
		Send a message and return the messages sent in response
		"""
		responses = []
		async with self._send_msg(msg) as aiter:
			async for resp in aiter:
				responses.append(resp)
		return responses

	@_make_aclosing
	async def _send_msg(self, msg: Message) -> AsyncGenerator[Message, None]:
		buff = self.buffer
		msg.pack(buff)
		await self._stream.send(buff[:].tobytes())
		del buff[:]
		if isinstance(msg, (Macro, Abort, Close)):
			return
		while 1:
			try:
				buff[:] = chunk = await self._stream.receive()
			except anyio.EndOfStream:
				break
			if len(chunk) == 0:
				break
			try:
				msg, size = Message.unpack(buff)
			except NeedsMore:
				continue
			del buff[:size]
			yield msg
			if isinstance(msg, typing.get_args(ResponseMessage) + (Negotiate, Skip)):
				break
		assert buff.filled == 0, buff[:].tobytes()

	async def send_and_expect(self, msg: Message, *exp: type[Message]|Message) -> None:
		"""
		Send a message and check the responses by type or equality
		"""
		resp = await self.send_msg(msg)
		assert len(resp) == len(exp), resp
		for r, e in zip(resp, exp):
			if isinstance(e, type):
				assert isinstance(r, e), f"expected {e}, got {type(r)}"
			else:
				assert r == e, r


def make_negotiate(options: int = 0, actions: int = 0x1ff) -> Negotiate:
	"""
	Construct a Negotiate message from integer flags

	Defaults to all actions, and no protocol options.
	"""
	return Negotiate(6, ActionFlags(actions), ProtocolFlags(options))
