from ipaddress import IPv4Address
from unittest.mock import call

import trio.testing

from kilter.protocol import *
from kilter.service.session import Phase
from kilter.service.session import Session

from . import AsyncTestCase
from .mock_editor import MockEditor

LOCALHOST = IPv4Address("127.0.0.1")


class SessionTests(AsyncTestCase):
	"""
	Tests for the kilter.service.session.Session class
	"""

	async def test_deliver_phases_1(self) -> None:
		"""
		Check that the phase progresses correctly when messages are delivered
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		assert session.phase == Phase.CONNECT

		await session.deliver(Helo("example.com"))
		assert session.phase == Phase.MAIL

		await session.deliver(EnvelopeFrom(b"test@example.com"))
		assert session.phase == Phase.ENVELOPE

		await session.deliver(Data())
		assert session.phase == Phase.HEADERS

		await session.deliver(Body(b""))
		assert session.phase == Phase.BODY

		await session.deliver(EndOfMessage(b""))
		assert session.phase == Phase.POST

	async def test_deliver_phases_2(self) -> None:
		"""
		Check that the phase progresses correctly when messages are delivered
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		assert session.phase == Phase.CONNECT

		await session.deliver(EnvelopeRecipient(b"test@example.com", []))
		assert session.phase == Phase.ENVELOPE

		await session.deliver(Header("To", b"test@example.com"))
		assert session.phase == Phase.HEADERS

		await session.deliver(EndOfHeaders())
		assert session.phase == Phase.BODY

	async def test_receive_ignore(self) -> None:
		"""
		Check that unwanted messages are ignored
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = "spam"

		async def test_filter() -> None:
			nonlocal result
			result = await session.envelope_from()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Helo("ham"))
			assert result == "spam"
			await session.deliver(EnvelopeFrom(b"eggs"))

		assert result == "eggs"

	async def test_await_helo(self) -> None:
		"""
		Check that the `helo()` method works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = "spam"

		async def test_filter() -> None:
			nonlocal result
			result = await session.helo()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Helo("eggs"))

		assert result == "eggs"

	async def test_await_helo_out_of_order(self) -> None:
		"""
		Check that awaiting `helo()` after later messages raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			await session.envelope_from()
			with self.assertRaises(RuntimeError) as acm:
				await session.helo()
			assert "before" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Helo("ham"))
			await session.deliver(EnvelopeFrom(b"eggs"))

	async def test_await_helo_missing(self) -> None:
		"""
		Check that receiving a later message than expected raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			with self.assertRaises(RuntimeError) as acm:
				await session.helo()
			assert "event not received" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EnvelopeFrom(b"eggs"))

	async def test_await_mail(self) -> None:
		"""
		Check that the `envelope_from()` method works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = "spam"

		async def test_filter() -> None:
			nonlocal result
			result = await session.envelope_from()

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EnvelopeFrom(b"eggs"))

		assert result == "eggs"

	async def test_await_from_out_of_order(self) -> None:
		"""
		Check that awaiting `envelope_from()` after later messages raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			await session.headers.collect()
			with self.assertRaises(RuntimeError) as acm:
				await session.envelope_from()
			assert "before" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"eggs"))
			await session.deliver(EndOfHeaders())

	async def test_await_from_missing(self) -> None:
		"""
		Check that receiving a later message than expected raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			with self.assertRaises(RuntimeError) as acm:
				await session.envelope_from()
			assert "event not received" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Data())

	async def test_await_rcpt(self) -> None:
		"""
		Check that the `envelope_recipients()` method works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter() -> None:
			async for rcpt in session.envelope_recipients():
				result.append(rcpt)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EnvelopeRecipient(b"spam", []))
			await session.deliver(EnvelopeRecipient(b"spam", []))
			await session.deliver(EnvelopeRecipient(b"eggs", []))
			await session.deliver(Data())

		assert result == ["spam", "spam", "eggs"]

	async def test_await_rcpt_out_of_order(self) -> None:
		"""
		Check that awaiting `envelope_recipients()` after later messages raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			await session.headers.collect()
			with self.assertRaises(RuntimeError) as acm:
				async for rcpt in session.envelope_recipients():
					pass
			assert "before" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"eggs"))
			await session.deliver(EndOfHeaders())

	async def test_await_extension(self) -> None:
		"""
		Check that the `extension()` method works as expected
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())
		result = []

		async def test_filter() -> None:
			result.append(await session.extension("SPAM"))
			result.append(await session.extension("MAIL"))
			result.append(await session.extension("RCPT"))
			result.append(await session.extension("RCPT"))

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Unknown(b"SPAM spam eggs"))
			await session.deliver(EnvelopeFrom(b"spam", [b"spam", b"eggs"]))
			await session.deliver(Unknown(b"HAM green eggs"))
			await session.deliver(EnvelopeRecipient(b"spam", [b"spam", b"eggs"]))
			await session.deliver(EnvelopeRecipient(b"spam", []))
			await session.deliver(Data())

		assert result == [
			b"SPAM spam eggs",
			b"MAIL FROM spam spam eggs",
			b"RCPT TO spam spam eggs",
			b"RCPT TO spam",
		]

	async def test_await_extension_out_of_order(self) -> None:
		"""
		Check that awaiting `extension()` after later messages raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			await session.headers.collect()
			with self.assertRaises(RuntimeError) as acm:
				await session.extension("TEST")
			assert "before" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Header("Spam", b"eggs"))
			await session.deliver(EndOfHeaders())

	async def test_await_extension_missing(self) -> None:
		"""
		Check that receiving a later message than expected raises RuntimeError
		"""
		session = Session(Connect("example.com", LOCALHOST, 1025), MockEditor())

		async def test_filter() -> None:
			with self.assertRaises(RuntimeError) as acm:
				await session.extension("TEST")
			assert "event not received" in str(acm.exception), str(acm.exception)

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(Data())

	async def test_await_change_sender(self) -> None:
		"""
		Check that `change_sender()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		async def test_filter() -> None:
			assert session.phase == Phase.CONNECT
			await session.change_sender("test@example.com")
			assert session.phase == Phase.POST
			await session.change_sender("test@example.com", "SPAM")

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_has_awaits([
			call(ChangeSender("test@example.com")),
			call(ChangeSender("test@example.com", "SPAM")),
		])

	async def test_await_add_recipient(self) -> None:
		"""
		Check that `add_recipient()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		async def test_filter() -> None:
			assert session.phase == Phase.CONNECT
			await session.add_recipient("test@example.com")
			assert session.phase == Phase.POST
			await session.add_recipient("test@example.com", "SPAM")

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_has_awaits([
			call(AddRecipient("test@example.com")),
			call(AddRecipientPar("test@example.com", "SPAM")),
		])

	async def test_await_remove_recipient(self) -> None:
		"""
		Check that `remove_recipient()` works as expected
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		async def test_filter() -> None:
			assert session.phase == Phase.CONNECT
			await session.remove_recipient("test@example.com")
			assert session.phase == Phase.POST

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()
			await session.deliver(EndOfMessage(b""))

		sender._asend.assert_has_awaits([
			call(RemoveRecipient("test@example.com")),
		])

	async def test_load_macros(self) -> None:
		"""
		Check that `deliver(Macro())` updates the macros dict
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		async def test_filter() -> None:
			self.assertDictEqual(session.macros, {})
			await session.helo()
			self.assertDictEqual(session.macros, {"{spam}": "yes", "{eggs}": "yes"})
			await session.envelope_from()
			self.assertDictEqual(session.macros, {"{spam}": "no", "{ham}": "maybe", "{eggs}": "yes"})

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()

			await session.deliver(Macro(Helo.ident, {"{spam}": "yes", "{eggs}": "yes"}))
			await session.deliver(Helo("test.example.com"))
			await session.deliver(Macro(Helo.ident, {"{spam}": "no", "{ham}": "maybe"}))
			await session.deliver(EnvelopeFrom(b"test@example.com"))

	async def test_delay(self) -> None:
		"""
		Check that delays/awaits don't create race conditions
		"""
		sender = MockEditor()
		session = Session(Connect("example.com", LOCALHOST, 1025), sender)

		async def test_filter() -> None:
			await trio.sleep(0.1)
			assert await session.helo() == "test.example.com"
			assert await session.envelope_from() == "test@example.com"

		async with trio.open_nursery() as tg:
			tg.start_soon(test_filter)
			await trio.testing.wait_all_tasks_blocked()

			await session.deliver(Helo("test.example.com"))
			await session.deliver(EnvelopeFrom(b"test@example.com"))
