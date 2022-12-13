# Copyright 2022 Dominik Sekotill <dom.sekotill@kodo.org.uk>
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Common helper utilities
"""

from __future__ import annotations

from typing import Generic
from typing import Optional
from typing import TypeVar

import anyio

T = TypeVar("T")


class Broadcast(anyio.Condition, Generic[T]):
	"""
	A reliable, blocking message queue for delivering to multiple listening tasks

	Listeners must acquire the lock (by using the `Broadcast` instance as a context manager)
	before calling `Broadcast.receive()` or it will fail.  If a listener is repeatedly
	awaiting messages in a loop, the loop should be inside the locked context or messages
	may be lost to race conditions.
	"""

	def __init__(self) -> None:
		super().__init__()
		self.obj: Optional[T] = None
		self.exc: Optional[BaseException|type[BaseException]] = None

	async def pre_receive_hook(self) -> None:
		"""
		A hook for subclasses to inject synchronisation instructions before awaiting objects
		"""  # noqa: D401

	async def post_send_hook(self) -> None:
		"""
		A hook for subclasses to inject synchronisation instructions after sending objects
		"""  # noqa: D401

	async def shutdown_hook(self) -> None:
		"""
		A hook for subclasses to inject cleanup or synchronisation instructions on close

		Users must ensure this method is called, especially if using a subclass which
		implements it.
		"""  # noqa: D401

	async def abort(self, exc: BaseException|type[BaseException]) -> None:
		"""
		Send a notification to all listeners to abort by raising an exception
		"""
		async with self:
			assert self.exc is None and self.obj is None
			self.exc = exc
			self.notify_all()
		await self._post()

	async def send(self, obj: T) -> None:
		"""
		Send a message object and block until all listeners have received it
		"""
		async with self:
			assert self.exc is None and self.obj is None
			self.obj = obj
			self.notify_all()
		await self._post()

	async def _post(self) -> None:
		await anyio.sleep(0.0)  # ensure listeners have opportunity to wait for locks
		await self.post_send_hook()

		# Ensure all listeners have had a chance to lock and process self.obj
		while 1:
			async with self:
				if self.statistics().lock_statistics.tasks_waiting:  # pragma: no-branch
					continue
				self.obj = self.exc = None
				break

	async def receive(self) -> T:
		"""
		Listen for a single message and return it once it arrives
		"""
		await self.pre_receive_hook()
		await self.wait()
		if self.exc is not None:
			raise self.exc
		assert self.obj is not None
		return self.obj
