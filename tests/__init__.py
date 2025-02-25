"""
A package of tests for kilter.service modules
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from collections.abc import Coroutine
from collections.abc import Iterator
from contextlib import contextmanager
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol
from typing import Self
from typing import TypeVar
from unittest import TestCase

import trio

E = TypeVar("E", bound=BaseException)

SyncTest = Callable[[TestCase], None]
AsyncTest = Callable[[TestCase], Coroutine[Any, Any, None]]

LIMIT_SCALE_FACTOR = float(os.environ.get("LIMIT_SCALE_FACTOR", 1))


if TYPE_CHECKING:
	class AssertRaisesContext(Protocol[E]):  # noqa: D101
		exception: E
		expected: type[BaseException] | tuple[type[BaseException], ...]
		msg: str|None


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

	@contextmanager
	def assertRaises(  # type: ignore[override]
		self,
		expected_exception: type[E]|tuple[type[E], ...],
		*,
		msg: str|None = None,
	) -> Iterator[AssertRaisesContext[E]]:
		"""
		Return a context manager that asserts a given exception is raised with the context

		Extends the base assertRaises with support for ExceptionGroups.  If at most one leaf
		exception is raised in the group and it matches the expected type, it will be
		treated as a successful failure.
		"""
		with super().assertRaises(expected_exception, msg=msg) as context:
			try:
				yield context
			except* expected_exception as grp:
				exc = [*_leaf_exc(grp)]
				assert len(exc) == 1
				raise exc[0] from grp


def _syncwrap(test: AsyncTest, time_limit: float) -> SyncTest:
	@functools.wraps(test)
	def wrap(self: TestCase) -> None:
		async def limiter() -> None:
			with trio.move_on_after(time_limit) as cancel_scope:
				await test(self)
			if cancel_scope.cancelled_caught:
				raise TimeoutError
		trio.run(limiter)
	return wrap


def _leaf_exc(group: BaseExceptionGroup) -> Iterator[BaseException]:
	for exc in group.exceptions:
		if isinstance(exc, BaseExceptionGroup):
			yield from _leaf_exc(exc)
		else:
			yield exc
