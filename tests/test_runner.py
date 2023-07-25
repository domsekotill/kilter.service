import trio

from kilter.protocol import *
from kilter.service import Runner
from kilter.service import Session
from kilter.service.session import Aborted

from . import AsyncTestCase
from .mock_stream import MockMessageStream


def make_negotiate(options: int = 0, actions: int = 0x1ff) -> Negotiate:
	"""
	Construct a Negotiate message from integer flags

	Defaults to all actions, and no protocol options.
	"""
	return Negotiate(6, ActionFlags(actions), ProtocolFlags(options))


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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(
				Negotiate(6, ActionFlags(0x1ff), ProtocolFlags(0xff3ff)),
				Negotiate(6, ActionFlags(0x1ff), ProtocolFlags.NONE),
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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Reject)

	async def test_post_header(self) -> None:
		"""
		Check that delaying return until a phase later than CONNECT sends Continue
		"""
		@Runner
		async def test_filter(session: Session) -> Accept:
			assert "test@example.com" == await session.envelope_from()
			return Accept()

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(
				make_negotiate(options=0xff3ff | ProtocolFlags.SKIP),
				Negotiate(6, ActionFlags(0x1ff), ProtocolFlags.SKIP),
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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
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

		async with MockMessageStream.started(runner) as stream_mock:
			await stream_mock.send_and_expect(
				make_negotiate(options=0xff3ff | ProtocolFlags.SKIP),
				Negotiate(6, ActionFlags(0x1ff), ProtocolFlags.SKIP),
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
		aborted = False
		helo = []
		envelope = ""

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal aborted
			nonlocal envelope
			helo.append(await session.helo())
			try:
				envelope = await session.envelope_from()
			except Aborted:
				aborted = True
				raise
			return Accept()

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(Helo("test.example.com"), Continue)

			assert [] == await stream_mock.send_msg(Abort())
			await stream_mock.send_and_expect(Helo("test.example.com"), Continue)
			await stream_mock.send_and_expect(EnvelopeFrom(b"sender@example.com"), Accept)

		assert aborted
		assert helo == ["test.example.com", "test.example.com"]
		assert envelope == "sender@example.com"

	async def test_abort_close(self) -> None:
		"""
		Check that a runner closes and does not restart when it receives an Abort + Close
		"""
		runs = 0

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal runs
			runs += 1
			await session.helo()
			return Accept()

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			assert [] == await stream_mock.send_msg(Abort())
			assert [] == await stream_mock.send_msg(Close())

		assert runs == 1

	async def test_bad_response(self) -> None:
		"""
		Check that when a filter returns an invalid response, it is converted to a failure
		"""
		@Runner  # type: ignore[arg-type]
		async def test_filter(session: Session) -> Skip:
			await session.helo()
			return Skip()

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)

			with self.assertWarns(UserWarning) as wcm:
				await stream_mock.send_and_expect(Helo("test.example.com"), TemporaryFailure)

			assert "expected a valid response" in str(wcm.warning)

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

		async with MockMessageStream.started(test_filter) as stream_mock:
			await stream_mock.send_and_expect(make_negotiate(), Negotiate)
			await stream_mock.send_and_expect(
				Macro(Connect.ident, {"{spam}": "yes", "{eggs}": "yes"}),
			)
			await stream_mock.send_and_expect(Connect("test.example.com"), Continue)
			await stream_mock.send_and_expect(
				Macro(Connect.ident, {"{spam}": "no", "{ham}": "maybe"}),
			)
			await stream_mock.send_and_expect(Helo("test.example.com"), Accept)
			await stream_mock.close()
