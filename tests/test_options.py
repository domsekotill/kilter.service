from unittest import TestCase

from kilter.protocol import Accept
from kilter.protocol import ActionFlags
from kilter.protocol import ProtocolFlags
from kilter.service import Session
from kilter.service import options

NO_CONNECT = ProtocolFlags.NO_CONNECT
NO_HELO = ProtocolFlags.NO_HELO
NO_SENDER = ProtocolFlags.NO_SENDER
NO_RECIPIENT = ProtocolFlags.NO_RECIPIENT
NO_BODY = ProtocolFlags.NO_BODY
NO_HEADERS = ProtocolFlags.NO_HEADERS
NO_END_OF_HEADERS = ProtocolFlags.NO_END_OF_HEADERS
NO_UNKNOWN = ProtocolFlags.NO_UNKNOWN
NO_DATA = ProtocolFlags.NO_DATA
SKIP = ProtocolFlags.SKIP
REJECTED_RECIPIENT = ProtocolFlags.REJECTED_RECIPIENT
NR_CONNECT = ProtocolFlags.NR_CONNECT
NR_HELO = ProtocolFlags.NR_HELO
NR_SENDER = ProtocolFlags.NR_SENDER
NR_RECIPIENT = ProtocolFlags.NR_RECIPIENT
NR_DATA = ProtocolFlags.NR_DATA
NR_UNKNOWN = ProtocolFlags.NR_UNKNOWN
NR_END_OF_HEADERS = ProtocolFlags.NR_END_OF_HEADERS
NR_BODY = ProtocolFlags.NR_BODY
NR_HEADER = ProtocolFlags.NR_HEADER
HEADER_LEADING_SPACE = ProtocolFlags.HEADER_LEADING_SPACE
MAX_DATA_SIZE_256K = ProtocolFlags.MAX_DATA_SIZE_256K
MAX_DATA_SIZE_1M = ProtocolFlags.MAX_DATA_SIZE_1M

ADD_HEADERS = ActionFlags.ADD_HEADERS
CHANGE_HEADERS = ActionFlags.CHANGE_HEADERS
CHANGE_BODY = ActionFlags.CHANGE_BODY
ADD_RECIPIENT = ActionFlags.ADD_RECIPIENT
ADD_RECIPIENT_PAR = ActionFlags.ADD_RECIPIENT_PAR
DELETE_RECIPIENT = ActionFlags.DELETE_RECIPIENT
QUARANTINE = ActionFlags.QUARANTINE
CHANGE_FROM = ActionFlags.CHANGE_FROM
SETSYMLIST = ActionFlags.SETSYMLIST


def resolve_opts(
	flags: options.FlagsTuple,
	starting: ProtocolFlags = ProtocolFlags.NONE,
) -> ProtocolFlags:
	"""
	Resolve the ProtocolFlags set & unset by a FlagsTuple object, from starting options
	"""
	return (starting | flags.set_options) & ~flags.unset_options


class Tests(TestCase):
	"""
	Tests for options decorators
	"""

	def test_get_flags(self) -> None:
		"""
		Check that get_flags works with both decorated and undecorated filters
		"""
		@options.modify_flags()
		async def decorated(session: Session) -> Accept:
			return Accept()

		async def undecorated(session: Session) -> Accept:
			return Accept()

		with self.subTest("decorated"):
			flags = options.get_flags(decorated)

			assert (flags.set_options & ~flags.unset_options) == ProtocolFlags.NONE
			assert flags.set_actions == ActionFlags.NONE

		with self.subTest("undecorated"):
			flags = options.get_flags(undecorated)

			assert (flags.set_options & ~flags.unset_options) == ProtocolFlags.NONE
			assert flags.set_actions == ActionFlags.ALL

	def test_modify_flags(self) -> None:
		"""
		Check that modify_flags compounds flags
		"""
		@options.modify_flags(
			set_options=SKIP,
			unset_options=HEADER_LEADING_SPACE,
			set_actions=ADD_HEADERS,
		)
		@options.modify_flags(
			set_options=NR_CONNECT,
			unset_options=SKIP,
		)
		@options.modify_flags(
			set_options=NR_CONNECT,
		)
		@options.modify_flags(
			set_actions=CHANGE_HEADERS,
		)
		async def filtr(session: Session) -> Accept:
			return Accept()

		flags = options.get_flags(filtr)

		assert flags.set_options == SKIP|NR_CONNECT, \
				f"{flags.set_options!r} is not SKIP"
		assert flags.unset_options == HEADER_LEADING_SPACE|SKIP, \
				f"{flags.unset_options!r} is not HEADER_LEADING_SPACE"
		assert flags.set_actions == ADD_HEADERS|CHANGE_HEADERS, \
				f"{flags.set_actions!r} is not ADD_HEADERS"

	def test_responds_to_connect(self) -> None:
		"""
		Check that decorating a filter with responds_to_connect disables NR_CONNECT
		"""
		@options.responds_to_connect()
		async def filtr(session: Session) -> Accept:
			return Accept()

		flags = options.get_flags(filtr)

		assert NR_CONNECT not in resolve_opts(flags)

	def test_examine_helo(self) -> None:
		"""
		Check that examine_helo unsets NO_HELO and toggles NR_HELO appropriately
		"""
		with self.subTest("can_respond=False"):
			@options.examine_helo(can_respond=False)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_HELO not in resolve_opts(flags, NO_HELO)
			assert NO_HELO not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_HELO not in resolve_opts(flags, NO_HELO)
			assert NR_HELO in resolve_opts(flags, NR_HELO)

		with self.subTest("can_respond=True"):
			@options.examine_helo(can_respond=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_HELO not in resolve_opts(flags, NO_HELO)
			assert NO_HELO not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_HELO not in resolve_opts(flags, NO_HELO)
			assert NR_HELO not in resolve_opts(flags, NR_HELO)

	def test_examine_sender(self) -> None:
		"""
		Check that examine_sender sets NO_SENDER, NR_SENDER & CHANGE_FROM appropriately
		"""
		with self.subTest("can_respond=False, can_replace=False"):
			@options.examine_sender(can_respond=False, can_replace=False)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_SENDER not in resolve_opts(flags, NO_SENDER)
			assert NO_SENDER not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_SENDER not in resolve_opts(flags, NO_SENDER)
			assert NR_SENDER in resolve_opts(flags, NR_SENDER)
			assert CHANGE_FROM not in flags.set_actions

		with self.subTest("can_respond=True, can_replace=True"):
			@options.examine_sender(can_respond=True, can_replace=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_SENDER not in resolve_opts(flags, NO_SENDER)
			assert NO_SENDER not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_SENDER not in resolve_opts(flags, NO_SENDER)
			assert NR_SENDER not in resolve_opts(flags, NR_SENDER)
			assert CHANGE_FROM in flags.set_actions

	def test_examine_recipients(self) -> None:
		"""
		Check that examine_recipients sets protocol options and actions as appropriate
		"""
		with self.subTest("all False"):
			@options.examine_recipients()
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_RECIPIENT not in resolve_opts(flags, NO_RECIPIENT)
			assert NO_RECIPIENT not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_RECIPIENT not in resolve_opts(flags, NO_RECIPIENT)
			assert NR_RECIPIENT in resolve_opts(flags, NR_RECIPIENT)
			assert REJECTED_RECIPIENT not in resolve_opts(flags, SKIP)
			assert REJECTED_RECIPIENT in resolve_opts(flags, REJECTED_RECIPIENT)

			assert ADD_RECIPIENT not in flags.set_actions
			assert ADD_RECIPIENT_PAR not in flags.set_actions
			assert DELETE_RECIPIENT not in flags.set_actions

		with self.subTest("can_respond, can_add, include_rejected"):
			@options.examine_recipients(can_respond=True, can_add=True, include_rejected=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_RECIPIENT not in resolve_opts(flags, NO_RECIPIENT)
			assert NO_RECIPIENT not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_RECIPIENT not in resolve_opts(flags, NO_RECIPIENT)
			assert NR_RECIPIENT not in resolve_opts(flags, NR_RECIPIENT)
			assert REJECTED_RECIPIENT in resolve_opts(flags, SKIP)

			assert ADD_RECIPIENT in flags.set_actions
			assert ADD_RECIPIENT_PAR not in flags.set_actions
			assert DELETE_RECIPIENT not in flags.set_actions

		with self.subTest("can_add, with_parameters"):
			@options.examine_recipients(can_add=True, with_parameters=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			# Skip repetitious checks…
			assert ADD_RECIPIENT in flags.set_actions
			assert ADD_RECIPIENT_PAR in flags.set_actions
			assert DELETE_RECIPIENT not in flags.set_actions

		with self.subTest("can_add, can_remove"):
			@options.examine_recipients(can_add=True, can_remove=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			# Skip repetitious checks…
			assert ADD_RECIPIENT in flags.set_actions
			assert ADD_RECIPIENT_PAR not in flags.set_actions
			assert DELETE_RECIPIENT in flags.set_actions

	def test_examine_headers(self) -> None:
		"""
		Check that examine_headers sets protocol options and actions as appropriate
		"""
		with self.subTest("all False"):
			@options.examine_headers()
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_HEADERS not in resolve_opts(flags, NO_HEADERS)
			assert NO_HEADERS not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_HEADER not in resolve_opts(flags, NO_HEADERS)
			assert NR_HEADER in resolve_opts(flags, NR_HEADER)
			assert HEADER_LEADING_SPACE not in resolve_opts(flags, ProtocolFlags.NONE)
			assert HEADER_LEADING_SPACE in resolve_opts(flags, HEADER_LEADING_SPACE)

			assert ADD_HEADERS not in flags.set_actions
			assert CHANGE_HEADERS not in flags.set_actions

		with self.subTest("can_respond=True, leading_space=True"):
			@options.examine_headers(can_respond=True, leading_space=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_HEADERS not in resolve_opts(flags, NO_HEADERS)
			assert NO_HEADERS not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_HEADER not in resolve_opts(flags, NO_HEADERS)
			assert NR_HEADER not in resolve_opts(flags, NR_HEADER)
			assert HEADER_LEADING_SPACE in resolve_opts(flags, ProtocolFlags.NONE)
			assert HEADER_LEADING_SPACE in resolve_opts(flags, HEADER_LEADING_SPACE)

			assert ADD_HEADERS not in flags.set_actions
			assert CHANGE_HEADERS not in flags.set_actions

		with self.subTest("can_add=True"):
			@options.examine_headers(can_add=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			# Skip repetitious checks…
			assert ADD_HEADERS in flags.set_actions
			assert CHANGE_HEADERS not in flags.set_actions

		with self.subTest("can_add=True, can_modify=True"):
			@options.examine_headers(can_add=True, can_modify=True)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			# Skip repetitious checks…
			assert ADD_HEADERS in flags.set_actions
			assert CHANGE_HEADERS in flags.set_actions

	def test_examine_body(self) -> None:
		"""
		Check that examine_body sets protocol options and actions as appropriate
		"""
		with self.subTest("all False"):
			@options.examine_body()
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_BODY not in resolve_opts(flags, NO_BODY)
			assert NO_BODY not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_BODY not in resolve_opts(flags, NO_BODY)
			assert NR_BODY in resolve_opts(flags, NR_BODY)
			assert MAX_DATA_SIZE_256K not in resolve_opts(flags, ProtocolFlags.NONE)
			assert MAX_DATA_SIZE_256K in resolve_opts(flags, MAX_DATA_SIZE_256K)
			assert MAX_DATA_SIZE_1M not in resolve_opts(flags, ProtocolFlags.NONE)
			assert MAX_DATA_SIZE_1M in resolve_opts(flags, MAX_DATA_SIZE_1M)

			assert CHANGE_BODY not in flags.set_actions

		with self.subTest("can_respond=True, can_replace=True, data_size=MDS_256K"):
			@options.examine_body(can_respond=True, can_replace=True, data_size=ProtocolFlags.MDS_256K)
			async def filtr(session: Session) -> Accept:
				return Accept()

			flags = options.get_flags(filtr)
			assert NO_BODY not in resolve_opts(flags, NO_BODY)
			assert NO_BODY not in resolve_opts(flags, ProtocolFlags.NONE)
			assert NR_BODY not in resolve_opts(flags, NO_BODY)
			assert NR_BODY not in resolve_opts(flags, NR_BODY)
			assert MAX_DATA_SIZE_256K in resolve_opts(flags, ProtocolFlags.NONE)
			assert MAX_DATA_SIZE_256K in resolve_opts(flags, MAX_DATA_SIZE_256K)
			assert MAX_DATA_SIZE_1M not in resolve_opts(flags, ProtocolFlags.NONE)
			assert MAX_DATA_SIZE_1M in resolve_opts(flags, MAX_DATA_SIZE_1M)

			assert CHANGE_BODY in flags.set_actions
