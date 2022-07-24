from collections.abc import AsyncGenerator
from types import TracebackType
from unittest.mock import AsyncMock

from kilter.protocol import Message


class MockEditor(AsyncGenerator[None, Message]):
	"""
	A mock of the interface used for sending update messages to the MTA
	"""

	def __init__(self) -> None:
		self._asend = AsyncMock()
		self._athrow = AsyncMock()

	async def asend(self, value: Message) -> None:  # noqa: D102
		await self._asend(value)

	async def athrow(  # noqa: D102
		self,
		e: type[BaseException]|BaseException,
		m: object = ...,
		t: TracebackType|None = None, /,
	) -> None:
		await self._athrow(self, e, m, t)
