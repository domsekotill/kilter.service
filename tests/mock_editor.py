from unittest.mock import AsyncMock

from kilter.protocol import Message


class MockEditor:
	"""
	A mock of the interface used for sending update messages to the MTA
	"""

	def __init__(self) -> None:
		self.mock_send = AsyncMock()

	async def send(self, value: Message) -> None:  # noqa: D102
		await self.mock_send(value)
