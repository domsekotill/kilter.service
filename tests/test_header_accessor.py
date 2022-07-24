from ipaddress import IPv4Address
from collections.abc import AsyncGenerator
from unittest.mock import call

import trio.testing
from kilter.service.session import Phase
from kilter.service import *
from kilter.protocol import *

from . import AsyncTestCase
from .mock_editor import MockEditor

LOCALHOST = IPv4Address("127.0.0.1")


class HeaderAccessorTests(AsyncTestCase):
	"""
	Tests for the kilter.service.session.HeaderAccessor class
	"""

	async def test_iterate_headers(self) -> None:
		"""
		Check that header iterator works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers:
					result.append(header.name)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())

			# try and throw the iterator off!
			await session.deliver(Header("Dead-Parrot", b"and spam"))

		assert result == ["Spam", "Spam", "Eggs"]

	async def test_break(self) -> None:
		"""
		Check that all headers are collected when breaking out of a loop
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result1 = []
		result2 = []

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers:
					result1.append(header.name)
					if header.name == "Eggs":
						break

			assert session.phase == Phase.BODY

			async with session.headers as headers:
				async for header in headers:
					result2.append(header.name)

			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(EndOfHeaders())

			# Try and throw the iterator off!  The filter should not await any further 
			# messages after the EndOfHeaders one.
			await session.deliver(Header("Dead-Parrot", b"and spam"))

		assert result1 == ["Spam", "Spam", "Eggs"]
		assert result2 == ["Spam", "Spam", "Eggs", "Spam"]

	async def test_collect(self) -> None:
		"""
		Check that all headers are collected when awaiting `collect()`
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.collect()

			async with session.headers as headers:
				async for header in headers:
					assert session.phase == Phase.BODY
					result.append(header.name)

			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())

		assert result == ["Spam", "Spam", "Eggs"]

	async def test_collect_no_eoh(self) -> None:
		"""
		Check that all headers are collected when awaiting `collect()` if EOH is missed
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.collect()

			async with session.headers as headers:
				async for header in headers:
					assert session.phase == Phase.BODY
					result.append(header.name)

			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(Body(b""))

		assert result == ["Spam", "Spam", "Eggs"]

	async def test_restrict(self) -> None:
		"""
		Check that `restrict()` works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers.restrict("Spam", "Ham"):
					result.append(header.name)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(Header("Ham", b"and spam"))
			await session.deliver(EndOfHeaders())

		assert result == ["Spam", "Spam", "Ham"], result

	async def test_delete(self) -> None:
		"""
		Check that `delete()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.collect()
			await session.headers.delete(Header("Spam", b"spam?"))
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(Header("Ham", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(ChangeHeader(1, "Spam", b""))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Eggs", b"and spam"),
			Header("Ham", b"and spam"),
		]

	async def test_update(self) -> None:
		"""
		Check that `update()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.update(Header("Spam", b"spam?"), b"no spam!")
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Spam", b"spam?"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(Header("Ham", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(ChangeHeader(1, "Spam", b"no spam!"))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Spam", b"no spam!"),
			Header("Eggs", b"and spam"),
			Header("Ham", b"and spam"),
		]

	async def test_insert_head(self) -> None:
		"""
		Check that `insert(..., START)` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(Header("Ham", b"and eggs"), START)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(InsertHeader(0, "Ham", b"and eggs"))
		assert result == [
			Header("Ham", b"and eggs"),
			Header("Spam", b"spam spam spam"),
			Header("Eggs", b"and spam"),
		]

	async def test_insert_tail(self) -> None:
		"""
		Check that `insert(..., END)` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(Header("Ham", b"and eggs"), END)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(AddHeader("Ham", b"and eggs"))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Eggs", b"and spam"),
			Header("Ham", b"and eggs"),
		]

	async def test_insert_before(self) -> None:
		"""
		Check that `insert(..., Before(...))` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(
				Header("Ham", b"and eggs"),
				Before(Header("Eggs", b"and spam")),
			)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(InsertHeader(1, "Ham", b"and eggs"))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Ham", b"and eggs"),
			Header("Eggs", b"and spam"),
		]

	async def test_insert_after(self) -> None:
		"""
		Check that `insert(..., After(...))` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(
				Header("Ham", b"and eggs"),
				After(Header("Spam", b"spam spam spam")),
			)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(InsertHeader(1, "Ham", b"and eggs"))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Ham", b"and eggs"),
			Header("Eggs", b"and spam"),
		]

	async def test_insert_after_end(self) -> None:
		"""
		Check that `insert(..., After(<last header>))` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(
				Header("Ham", b"and eggs"),
				After(Header("Eggs", b"and spam")),
			)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_awaited_with(AddHeader("Ham", b"and eggs"))
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Eggs", b"and spam"),
			Header("Ham", b"and eggs"),
		]

	async def test_multiple_edit(self) -> None:
		"""
		Check that multiple edits in a filter work as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)
		result = []

		async def test_filter(session: Session) -> Accept:
			await session.headers.insert(
				Header("Ham", b"and eggs"),
				Before(Header("Eggs", b"and spam")),
			)
			await session.headers.insert(
				Header("Ham", b"and spam"),
				Before(Header("Eggs", b"and spam")),
			)
			async with session.headers as headers:
				async for header in headers:
					result.append(header)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"spam spam spam"))
			await session.deliver(Header("Eggs", b"and spam"))
			await session.deliver(EndOfHeaders())
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_has_awaits([
			call(InsertHeader(1, "Ham", b"and eggs")),
			call(InsertHeader(2, "Ham", b"and spam")),
		])
		assert result == [
			Header("Spam", b"spam spam spam"),
			Header("Ham", b"and eggs"),
			Header("Ham", b"and spam"),
			Header("Eggs", b"and spam"),
		]

	async def test_asend(self) -> None:
		"""
		Check that the AsyncGenerator-required method `asend()` works
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				assert Header("From", b"test@example.com") == await headers.asend()
				assert Header("To", b"test@example.com") == await headers.asend(None)
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("From", b"test@example.com"))
			await session.deliver(Header("To", b"test@example.com"))
			await session.deliver(EndOfHeaders())

	async def test_athrow_type(self) -> None:
		"""
		Check that the AsyncGenerator-required method `athrow()` works
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				await headers.asend()
				with self.assertRaises(ValueError):
					await headers.athrow(ValueError)
				with self.assertRaises(StopAsyncIteration):
					await headers.asend()
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("From", b"test@example.com"))
			await session.deliver(Header("To", b"test@example.com"))
			await session.deliver(EndOfHeaders())

	async def test_athrow_type_msg(self) -> None:
		"""
		Check that the AsyncGenerator-required method `athrow()` works
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				await headers.asend()
				with self.assertRaises(ValueError) as acm:
					await headers.athrow(ValueError, "a message")
				assert "a message" == str(acm.exception), str(acm.exception)
				with self.assertRaises(StopAsyncIteration):
					await headers.asend()
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("From", b"test@example.com"))
			await session.deliver(Header("To", b"test@example.com"))
			await session.deliver(EndOfHeaders())

	async def test_athrow_inst(self) -> None:
		"""
		Check that the AsyncGenerator-required method `athrow()` works
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				await headers.asend()
				with self.assertRaises(ValueError) as acm:
					await headers.athrow(ValueError("a message"))
				assert "a message" == str(acm.exception), str(acm.exception)
				with self.assertRaises(StopAsyncIteration):
					await headers.asend()
			return Accept()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter, session)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("From", b"test@example.com"))
			await session.deliver(Header("To", b"test@example.com"))
			await session.deliver(EndOfHeaders())
