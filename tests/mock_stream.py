from __future__ import annotations

import typing
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from types import TracebackType
from typing import TYPE_CHECKING
from typing import AsyncContextManager
from typing import TypeVar

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.stapled import StapledByteStream
from anyio.streams.stapled import StapledObjectStream
from async_generator import aclosing

from kilter.protocol import *
from kilter.protocol.buffer import SimpleBuffer
from kilter.service import ResponseMessage

P = typing.ParamSpec("P")
SendT = typing.TypeVar("SendT")
YieldT = typing.TypeVar("YieldT")


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

	if TYPE_CHECKING:
		Self = TypeVar("Self", bound="MockMessageStream")

	def __init__(self) -> None:
		self.buffer = SimpleBuffer(1024)
		self.closed = False

	async def __aenter__(self: Self) -> Self:
		send_obj, recv_bytes = anyio.create_memory_object_stream(5, bytes)
		send_bytes, recv_obj = anyio.create_memory_object_stream(5, bytes)

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

	async def abort(self) -> None:
		"""
		Send Abort and close the stream
		"""
		try:
			resp = await self.send_msg(Abort())
		except anyio.BrokenResourceError:
			return
		assert len(resp) == 0, resp
		await self.close()

	async def close(self) -> None:
		"""
		Send Close and close the stream
		"""
		if self.closed:
			return
		resp = await self.send_msg(Close())
		assert len(resp) == 0, resp
		await self._stream.aclose()
		self.closed = True

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
		if isinstance(msg, (Abort, Close)):
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
