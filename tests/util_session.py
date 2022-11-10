from __future__ import annotations

from collections.abc import Coroutine as _Coroutine
from functools import wraps
from typing import Any
from typing import Callable
from typing import TypeAlias
from typing import TypeVar

from kilter.service import Session

T = TypeVar("T")
Decorator: TypeAlias = Callable[[T], T]
Coroutine: TypeAlias = _Coroutine[Any, Any, T]


def with_session(session: Session) -> Decorator[Callable[[], Coroutine[None]]]:
	"""
	Run an async filter function within a Session context

	This is for testing only; the Session instance is assumed to be available to the filter
	function as a closure variable.
	"""
	def deco(func: Callable[[], Coroutine[None]]) -> Callable[[], Coroutine[None]]:
		@wraps(func)
		async def wrapper() -> None:
			async with session:
				await func()
		return wrapper
	return deco
