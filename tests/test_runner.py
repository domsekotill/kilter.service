import trio.testing

from kilter.protocol import *
from kilter.service import Runner
from kilter.service import Session

from . import AsyncTestCase
from .mock_stream import MockMessageStream


class RunnerTests(AsyncTestCase):
	"""
	Tests for the Runner class
	"""

	async def test_helo(self) -> None:
		"""
		Check that awaiting Session.helo() responds to Connect with Continue
		"""
		hostname = ""

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal hostname
			hostname = await session.helo()
			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(
				Negotiate(6, 0x1ff, 0xff3ff),
				Negotiate(6, 0x1ff, 0x00000),
			)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)

			assert hostname == ""
			await stream_mock.send_and_expect(Helo("test.example.com"), Accept)
			assert hostname == "test.example.com"

	async def test_respond_to_peer(self) -> None:
		"""
		Check that returning before engaging with async session features works
		"""

		@Runner
		async def test_filter(session: Session) -> Reject:
			return Reject()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Reject)

	async def test_post_header(self) -> None:
		"""
		Check that delaying return until a phase later than CONNECT sends Continue
		"""
		@Runner
		async def test_filter(session: Session) -> Accept:
			assert "test@example.com" == await session.envelope_from()
			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(Helo("test.example.com"), Continue)
			await stream_mock.send_and_expect(EnvelopeFrom(b"test@example.com"), Accept)

	async def test_body_all(self) -> None:
		"""
		Check that the whole body is processes when Continue is passed
		"""
		contents = b""

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal contents
			async with session.body as body:
				async for chunk in body:
					await trio.sleep(0)
					contents += chunk
			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(Body(b"This is a "), Continue)
			await stream_mock.send_and_expect(Body(b"message sent "), Continue)
			await stream_mock.send_and_expect(Body(b"in multiple chunks. "), Continue)
			await stream_mock.send_and_expect(EndOfMessage(b"Bye"), Accept)

		assert contents == b"This is a message sent in multiple chunks. Bye", contents

	async def test_body_skip(self) -> None:
		"""
		Check that Skip is returned once a body loop is broken
		"""
		contents = b""

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal contents
			async with session.body as body:
				async for chunk in body:
					contents += chunk
					if b"message" in chunk.tobytes():
						break

			# Move phase onto POST
			await session.change_sender("test@example.com")

			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(
				Negotiate(6, 0x1ff, 0xff3ff | ProtocolFlags.SKIP),
				Negotiate(6, 0x1ff, 0x00000 | ProtocolFlags.SKIP),
			)

			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)

			await stream_mock.send_and_expect(Body(b"This is a "), Continue)
			await stream_mock.send_and_expect(Body(b"message sent "), Skip)
			await stream_mock.send_and_expect(Body(b"in multiple chunks. "), Skip)
			await stream_mock.send_and_expect(
				EndOfMessage(b"Bye"),
				ChangeSender("test@example.com"), Accept,
			)

		assert contents == b"This is a message sent ", contents

	async def test_body_fake_skip(self) -> None:
		"""
		Check that Skip is NOT returned if not accepted by an MTA
		"""
		contents = b""

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal contents
			async with session.body as body:
				async for chunk in body:
					contents += chunk
					if b"message" in chunk.tobytes():
						break

			# Move phase onto POST
			await session.change_sender("test@example.com")

			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(Body(b"This is a "), Continue)
			await stream_mock.send_and_expect(Body(b"message sent "), Continue)
			await stream_mock.send_and_expect(Body(b"in multiple chunks. "), Continue)
			await stream_mock.send_and_expect(
				EndOfMessage(b"Bye"),
				ChangeSender("test@example.com"), Accept,
			)

		assert contents == b"This is a message sent ", contents

	async def test_multiple(self) -> None:
		"""
		Check that multiple filters receive the messages they expect
		"""
		hostname = ""
		contents1 = b""
		contents2 = b""

		async def test_filter1(session: Session) -> Reject:
			nonlocal hostname
			nonlocal contents1

			hostname = await session.helo()

			async with session.body as body:
				async for chunk in body:
					await trio.sleep(0)
					contents1 += chunk

			return Reject()

		async def test_filter2(session: Session) -> Accept:
			nonlocal contents2

			async with session.body as body:
				async for chunk in body:
					await trio.sleep(0)
					contents2 += chunk
					if b"message" in chunk.tobytes():
						break

			return Accept()

		runner = Runner(test_filter1, test_filter2)

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(runner, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(
				Negotiate(6, 0x1ff, 0xff3ff | ProtocolFlags.SKIP),
				Negotiate(6, 0x1ff, 0x00000 | ProtocolFlags.SKIP),
			)

			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(Helo("test.example.com"), Continue)

			await stream_mock.send_and_expect(Body(b"This is a "), Continue)
			await stream_mock.send_and_expect(Body(b"message sent "), Continue)
			await stream_mock.send_and_expect(Body(b"in multiple chunks. "), Continue)
			await stream_mock.send_and_expect(EndOfMessage(b"Bye"), Reject)

		assert hostname == "test.example.com", hostname
		assert contents1 == b"This is a message sent in multiple chunks. Bye", contents1
		assert contents2 == b"This is a message sent ", contents2

	async def test_abort(self) -> None:
		"""
		Check that a runner closes cleanly when it receives an Abort
		"""
		cancelled = False

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal cancelled
			try:
				await session.helo()
			except trio.Cancelled:
				cancelled = True
				raise
			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.abort()

		assert cancelled

	async def test_bad_response(self) -> None:
		"""
		Check that a runner closes cleanly when it receives an Abort
		"""
		@Runner
		async def test_filter(session: Session) -> Skip:
			await session.helo()
			return Skip()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)

			with self.assertWarns(UserWarning) as wcm:
				await stream_mock.send_and_expect(Helo("test.example.com"), TemporaryFailure)

			assert "expected a final response" in str(wcm.warning)

	async def test_macros(self) -> None:
		"""
		Check that delivered macros are available
		"""
		@Runner
		async def test_filter(session: Session) -> Accept:
			self.assertDictEqual(session.macros, {"{spam}": "yes", "{eggs}": "yes"})
			await session.helo()
			self.assertDictEqual(session.macros, {"{spam}": "no", "{ham}": "maybe", "{eggs}": "yes"})
			return Accept()

		async with trio.open_nursery() as tg, MockMessageStream() as stream_mock:
			tg.start_soon(test_filter, stream_mock.peer_stream)
			await trio.testing.wait_all_tasks_blocked()

			await stream_mock.send_and_expect(Negotiate(6, 0x1ff, 0), Negotiate)
			await stream_mock.send_and_expect(
				Macro(Connect.ident, {"{spam}": "yes", "{eggs}": "yes"}),
			)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(
				Macro(Connect.ident, {"{spam}": "no", "{ham}": "maybe"}),
			)
			await stream_mock.send_and_expect(Helo("test.example.com"), Accept)
			await stream_mock.close()
