import trio.testing

from kilter.service.util import Broadcast

from . import AsyncTestCase


class BroadcastTests(AsyncTestCase):
	"""
	Tests for the kilter.service.sync.Broadcast class
	"""

	async def test_send_no_listeners(self) -> None:
		"""
		Check that sending a message with no listeners does not block
		"""
		broadcast = Broadcast[int]()

		with trio.move_on_after(2.0) as cancel_scope:
			await broadcast.send(1)

		assert not cancel_scope.cancelled_caught

	async def test_send_one_listener(self) -> None:
		"""
		Check that sending a message to a single listener works
		"""
		broadcast = Broadcast[int]()
		messages = list[int]()

		async def listener() -> None:
			async with broadcast:
				messages.append(await broadcast.receive())

		async with trio.open_nursery() as task_group:
			task_group.start_soon(listener)
			await trio.testing.wait_all_tasks_blocked()

			await broadcast.send(1)
			await broadcast.send(2)

		assert messages == [1]

	async def test_send_multiple_listeners(self) -> None:
		"""
		Check that sending a message to multiple listeners works
		"""
		broadcast = Broadcast[int]()
		messages = list[int]()

		async def listener() -> None:
			async with broadcast:
				messages.append(await broadcast.receive())

		async with trio.open_nursery() as task_group:
			for _ in range(4):
				task_group.start_soon(listener)
			await trio.testing.wait_all_tasks_blocked()

			await broadcast.send(1)
			await broadcast.send(2)

		assert messages == [1, 1, 1, 1]

	async def test_recieve_loop(self) -> None:
		"""
		Check that receiving multiple messages in a loop works
		"""
		broadcast = Broadcast[int]()
		messages = list[int|str]()

		async def listener() -> None:
			async with broadcast:
				msg = 0
				while msg < 4:
					msg = await broadcast.receive()
					messages.append(msg)

		async with trio.open_nursery() as task_group:
			task_group.start_soon(listener)
			task_group.start_soon(listener)
			await trio.testing.wait_all_tasks_blocked()

			for n in range(1, 10):  # Deliberately higher than the listeners go
				await broadcast.send(n)

		assert messages == [1, 1, 2, 2, 3, 3, 4, 4]

	async def test_abort(self) -> None:
		"""
		Check that aborting with multiple listeners works
		"""
		broadcast = Broadcast[int]()

		async def listener() -> None:
			async with broadcast:
				with self.assertRaises(ValueError):
					_ = await broadcast.receive()

		async with trio.open_nursery() as task_group:
			task_group.start_soon(listener)
			task_group.start_soon(listener)
			await trio.testing.wait_all_tasks_blocked()

			await broadcast.abort(ValueError)
