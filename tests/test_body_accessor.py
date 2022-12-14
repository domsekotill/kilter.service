from ipaddress import IPv4Address
from pathlib import Path

import trio.testing

from kilter.protocol import *
from kilter.service import *

from . import AsyncTestCase
from .mock_editor import MockEditor
from .util_session import with_session

LOCALHOST = IPv4Address("127.0.0.1")
THIS_MODULE = Path(__file__)


class HeaderAccessorTests(AsyncTestCase):
	"""
	Tests for the kilter.service.session.HeaderAccessor class
	"""

	async def test_iterate_body(self) -> None:
		"""
		Check that the body iterator works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = b""

		@with_session(session)
		async def test_filter() -> None:
			nonlocal result
			async with session.body as body:
				async for chunk in body:
					result += chunk

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Body(b"Spam, "))
			await session.deliver(Body(b"spam, "))
			await session.deliver(EndOfMessage(b"and eggs"))

		assert result == b"Spam, spam, and eggs"

	async def test_break(self) -> None:
		"""
		Check that Body (and EOM) messages are skipped after breaking out of a loop
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result1 = b""
		result2 = b""

		@with_session(session)
		async def test_filter() -> None:
			nonlocal result1
			nonlocal result2

			async with session.body as body:
				async for chunk in body:
					if chunk[:4] == b"spam":
						break
					result1 += chunk

			async with session.body as body:
				async for chunk in body:
					result2 += chunk


		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			assert Continue == await session.deliver(Body(b"Spam, "))
			assert Skip == await session.deliver(Body(b"spam, "))
			assert Skip == await session.deliver(Body(b"spam, "))
			assert Continue == await session.deliver(EndOfMessage(b"and eggs"))

		assert result1 == b"Spam, "
		assert result2 == b""

	async def test_write(self) -> None:
		"""
		Check that `write()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		# Temporary hack for missing equality check in kilter.protocol
		def _eq(s: ReplaceBody, o: object) -> bool:
			if not isinstance(o, type(s)):
				return NotImplemented
			return s.content == o.content
		ReplaceBody.__eq__ = _eq  # type: ignore

		@with_session(session)
		async def test_filter() -> None:
			await session.body.write(b"A new message")

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(ReplaceBody(b"A new message"))

	async def test_write_in_iter_context(self) -> None:
		"""
		Check that `write()` in an async with context issues a warning
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		# Temporary hack for missing equality check in kilter.protocol
		def _eq(s: ReplaceBody, o: object) -> bool:
			if not isinstance(o, type(s)):
				return NotImplemented
			return s.content == o.content
		ReplaceBody.__eq__ = _eq  # type: ignore

		@with_session(session)
		async def test_filter() -> None:
			async with session.body:
				with self.assertWarns(UserWarning) as cm:
					await session.body.write(b"A new message")
				assert THIS_MODULE.samefile(cm.filename)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(ReplaceBody(b"A new message"))
