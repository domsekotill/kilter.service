"""
A package of tests for kilter.service modules
"""

import functools
import os
from collections.abc import Callable
from collections.abc import Coroutine
from inspect import iscoroutinefunction
from typing import Any
from unittest import TestCase

import trio

SyncTest = Callable[[TestCase], None]
AsyncTest = Callable[[TestCase], Coroutine[Any, Any, None]]

LIMIT_SCALE_FACTOR = float(os.environ.get("LIMIT_SCALE_FACTOR", 1))


class AsyncTestCase(TestCase):
	"""
	A variation of `unittest.TestCase` with support for awaitable (async) test functions
	"""

	@classmethod
	def __init_subclass__(cls, time_limit: float = 0.5, **kwargs: Any) -> None:
		super().__init_subclass__(**kwargs)
		for name, value in ((n, getattr(cls, n)) for n in dir(cls)):
			if name.startswith("test_") and iscoroutinefunction(value):
				setattr(cls, name, _syncwrap(value, time_limit * LIMIT_SCALE_FACTOR))


def _syncwrap(test: AsyncTest, time_limit: float) -> SyncTest:
	@functools.wraps(test)
	def wrap(self: TestCase) -> None:
		async def limiter() -> None:
			with trio.fail_after(time_limit) as cancel_scope:
				await test(self)
			if cancel_scope.cancelled_caught:
				raise TimeoutError
		trio.run(limiter)
	return wrap
