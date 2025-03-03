import trio

from kilter.protocol import *
from kilter.service import Runner
from kilter.service import Session
from kilter.service.options import examine_headers
from kilter.service.options import examine_helo
from kilter.service.options import responds_to_connect
from kilter.service.runner import NegotiationError
from kilter.service.session import Aborted

from . import AsyncTestCase
from .mock_stream import MockMessageStream
from .mock_stream import make_negotiate


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

	async def test_multiple_same(self) -> None:
		"""
		Check that an exception is raised when a filter is added multiple times
		"""
		async def test_filter(session: Session) -> Accept:
			return Accept()

		with self.assertWarns(UserWarning):
			Runner(test_filter, test_filter)

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


class RunnerNegotiateTests(AsyncTestCase):
	"""
	Tests for option negotiation
	"""

	async def test_default(self) -> None:
		"""
		Check that filters without option decoration negotiate for all stages and responses
		"""
		@Runner
		async def test_filter(session: Session) -> Accept:
			return Accept()

		with self.subTest("no options"):
			async with MockMessageStream.started(test_filter) as stream_mock:
				await stream_mock.send_and_expect(
					make_negotiate(),
					make_negotiate(),
				)

		with self.subTest("options"):
			offered = (
				ProtocolFlags.NO_CONNECT | ProtocolFlags.NR_CONNECT |
				ProtocolFlags.NO_HELO | ProtocolFlags.NR_HELO |
				ProtocolFlags.SKIP
			)

			async with MockMessageStream.started(test_filter) as stream_mock:
				await stream_mock.send_and_expect(
					make_negotiate(options=offered),
					make_negotiate(options=ProtocolFlags.SKIP),
				)

	async def test_options_decorated(self) -> None:
		"""
		Check that decorated filters negotiate for the minimum required stages and responses
		"""
		@Runner
		@responds_to_connect()
		async def test_filter(session: Session) -> Accept:
			return Accept()

		async with MockMessageStream.started(test_filter) as stream_mock:
			offered = (
				ProtocolFlags.NO_CONNECT | ProtocolFlags.NR_CONNECT |
				ProtocolFlags.NO_HELO | ProtocolFlags.SKIP
			)

			await stream_mock.send_and_expect(
				make_negotiate(options=offered),
				make_negotiate(actions=0, options=ProtocolFlags.NO_HELO|ProtocolFlags.SKIP),
			)

	async def test_options_nr(self) -> None:
		"""
		Check that decorated filters unset NR_* options when not responding
		"""
		@Runner
		@examine_helo(can_respond=True)
		async def test_filter(session: Session) -> Accept:
			await session.helo()
			return Accept()

		async with MockMessageStream.started(test_filter) as stream_mock:
			offered = (
				ProtocolFlags.NO_CONNECT | ProtocolFlags.NR_CONNECT |
				ProtocolFlags.NO_HELO | ProtocolFlags.NR_HELO
			)

			await stream_mock.send_and_expect(
				make_negotiate(options=offered),
				make_negotiate(actions=0, options=ProtocolFlags.NR_CONNECT),
			)

	async def test_missing_needed(self) -> None:
		"""
		Check that when requiring options not offered by an MTA, NegotiateError is raised
		"""
		@Runner
		@responds_to_connect()
		@examine_headers(can_add=True, leading_space=True)
		async def test_filter(session: Session) -> Accept:
			return Accept()

		with self.subTest("missing actions"), self.assertRaises(NegotiationError):
			async with MockMessageStream.started(test_filter) as stream_mock:
				await stream_mock.send_msg(
					make_negotiate(actions=0, options=ProtocolFlags.HEADER_LEADING_SPACE),
				)

		with self.subTest("missing options"), self.assertRaises(NegotiationError):
			async with MockMessageStream.started(test_filter) as stream_mock:
				await stream_mock.send_msg(
					make_negotiate(options=0),
				)


class SessionReuseTests(AsyncTestCase):
	"""
	Tests for sessions handling multiple messages

	Most of these added as regression tests for #21
	"""

	QUOTE1 = """
	Strange women lying in ponds, distributing swords, is no basis for a system of
	government!
	""".encode("utf-8")

	QUOTE2 = """
	Supreme executive power derives from a mandate from the masses,
	not from some farcical aquatic ceremony.
	""".encode("utf-8")

	QUOTE3 = """
	He’s not the Messiah; he’s a very naughty boy!
	""".encode("utf-8")

	async def test_mail(self) -> None:
		"""
		Check each message gets its own MAIL command and a copy of the HELO/EHLO message
		"""
		counter = 0

		@Runner
		async def test_filter(session: Session) -> Accept:
			nonlocal counter
			counter += 1
			assert await session.helo() == "test.example.com"
			assert await session.envelope_from() == "test@example.com"
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			await stream.send_and_expect(EnvelopeFrom(b"test@example.com"), Accept)
			await stream.send_msg(Abort())
			await stream.send_and_expect(EnvelopeFrom(b"test@example.com"), Accept)

		assert counter == 2

	async def test_headers(self) -> None:
		"""
		Check that the header accessor is reset for each message
		"""
		results = list[bytes]()

		@Runner
		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers.restrict("X-Test"):
					results.append(header.value)
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			async with stream.envelope(b"test1@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"spam"), Continue)
				await stream.send_and_expect(Header("X-Test", b"ham"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Accept)
			async with stream.envelope(b"test2@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"eggs"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Accept)

		assert results == [b"spam", b"ham", b"eggs"], results

	async def test_body(self) -> None:
		"""
		Check that the body accessor is reset for each message
		"""
		results = list[bytes]()

		@Runner
		async def test_filter(session: Session) -> Accept:
			async with session.body as body:
				results.extend([cnk.tobytes() async for cnk in body])
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Body(self.QUOTE1), Continue)
				await stream.send_and_expect(Body(self.QUOTE2), Continue)
				await stream.send_and_expect(EndOfMessage(b""), Accept)
			assert results == [self.QUOTE1, self.QUOTE2, b""]

			del results[:]

			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Body(self.QUOTE3), Continue)
				await stream.send_and_expect(EndOfMessage(b""), Accept)
			assert results == [self.QUOTE3, b""]

	async def test_combined(self) -> None:
		"""
		Check that headers and body accessors are reset for each message
		"""
		header_list = list[bytes]()
		body_list = list[bytes]()

		@Runner
		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers.restrict("X-Test"):
					header_list.append(header.value)
			async with session.body as body:
				body_list.extend([cnk.tobytes() async for cnk in body])
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"spam"), Continue)
				await stream.send_and_expect(Header("X-Test", b"ham"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Continue)
				await stream.send_and_expect(Body(self.QUOTE1), Continue)
				await stream.send_and_expect(Body(self.QUOTE2), Continue)
				await stream.send_and_expect(EndOfMessage(b""), Accept)
			assert header_list == [b"spam", b"ham"]
			assert body_list == [self.QUOTE1, self.QUOTE2, b""]

			del header_list[:]
			del body_list[:]

			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"eggs"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Continue)
				await stream.send_and_expect(Body(self.QUOTE3), Continue)
				await stream.send_and_expect(EndOfMessage(b""), Accept)
			assert header_list == [b"eggs"]
			assert body_list == [self.QUOTE3, b""]

	async def test_abort_session(self) -> None:
		"""
		Check that aborting a session clears all session state
		"""
		results = list[bytes]()

		@Runner
		async def test_filter(session: Session) -> Accept:
			async with session.headers as headers:
				async for header in headers.restrict("X-Test"):
					results.append(header.value)
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			async with stream.envelope(b"test1@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"spam"), Continue)
				await stream.send_and_expect(Header("X-Test", b"ham"), Continue)
			await stream.send_msg(Abort())
			async with stream.envelope(b"test2@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"eggs"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Accept)

		assert results == [b"spam", b"ham", b"eggs"], results

	async def test_reject(self) -> None:
		"""
		Check that rejecting a message does not close a session
		"""
		header_list = list[bytes]()

		@Runner
		async def test_filter(session: Session) -> Accept|Reject:
			async with session.headers as headers:
				async for header in headers.restrict("X-Test"):
					header_list.append(header.value)
					if header.value == b"ham":
						return Reject()
			return Accept()

		async with MockMessageStream.connected(test_filter) as stream:
			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"spam"), Continue)
				await stream.send_and_expect(Header("X-Test", b"ham"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Reject)
			assert header_list == [b"spam", b"ham"]

			del header_list[:]

			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"eggs"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Accept)
			assert header_list == [b"eggs"]

	async def test_multi_reject(self) -> None:
		"""
		Check that a rejection from one filter of several rejects the current message
		"""
		async def test_filter1(session: Session) -> Accept|Reject:
			async with session.headers as headers:
				async for header in headers.restrict("X-Test"):
					if header.value == b"ham":
						return Reject()
			return Accept()

		async def test_filter2(session: Session) -> Accept|Reject:
			await session.headers.collect()
			return Accept()

		async with MockMessageStream.connected(Runner(test_filter1, test_filter2)) as stream:
			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"spam"), Continue)
				await stream.send_and_expect(Header("X-Test", b"ham"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Reject)

			async with stream.envelope(b"test@example.com", b"test@example.com"):
				await stream.send_and_expect(Header("X-Test", b"eggs"), Continue)
				await stream.send_and_expect(EndOfHeaders(), Accept)
