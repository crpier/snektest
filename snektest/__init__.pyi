from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any, overload

from snektest.annotations import Coroutine
from snektest.assertions import (
    assert_eq,
    assert_false,
    assert_ge,
    assert_gt,
    assert_in,
    assert_is,
    assert_is_none,
    assert_is_not,
    assert_is_not_none,
    assert_isinstance,
    assert_le,
    assert_len,
    assert_lt,
    assert_ne,
    assert_not_in,
    assert_not_isinstance,
    assert_raises,
    assert_true,
    fail,
)
from snektest.decorators import SearchStrategy
from snektest.models import Param, UnreachableError
from snektest.models import Scope as Scope

__all__ = [
    "Param",
    "Scope",
    "UnreachableError",
    "assert_eq",
    "assert_false",
    "assert_ge",
    "assert_gt",
    "assert_in",
    "assert_is",
    "assert_is_none",
    "assert_is_not",
    "assert_is_not_none",
    "assert_isinstance",
    "assert_le",
    "assert_len",
    "assert_lt",
    "assert_ne",
    "assert_not_in",
    "assert_not_isinstance",
    "assert_raises",
    "assert_true",
    "fail",
    "load_fixture",
    "session_fixture",
    "test",
]

@overload
def test() -> Callable[
    [Callable[[], Coroutine[None] | None]], Callable[[], Coroutine[None] | None]
]: ...
@overload
def test[T](
    param: list[Param[T]],
) -> Callable[
    [Callable[[T], Coroutine[None] | None]], Callable[[T], Coroutine[None] | None]
]: ...
@overload
def test[T1, T2](
    param1: list[Param[T1]],
    param2: list[Param[T2]],
) -> Callable[
    [Callable[[T1, T2], Coroutine[None] | None]],
    Callable[[T1, T2], Coroutine[None] | None],
]: ...
def session_fixture[T, R: AsyncGenerator[T] | Generator[T]]() -> Callable[  # pyright: ignore[reportGeneralTypeIssues]
    [Callable[[], R]], Callable[[], R]
]: ...
@overload
def load_fixture[R](
    fixture_gen: Generator[R],
) -> R: ...
@overload
def load_fixture[R](
    fixture_gen: AsyncGenerator[R],
) -> Coroutine[R]: ...
@overload
def test_hypothesis[T1](
    strategy1: SearchStrategy[T1],
    /,
) -> Callable[
    [Callable[[T1], Coroutine[None] | None]],
    Callable[[], Coroutine[None] | None],
]: ...
@overload
def test_hypothesis[T1, T2](
    strategy1: SearchStrategy[T1],
    strategy2: SearchStrategy[T2],
    /,
) -> Callable[
    [Callable[[T1, T2], Coroutine[None] | None]],
    Callable[[], Coroutine[None] | None],
]: ...
@overload
def test_hypothesis[T1, T2, T3](
    strategy1: SearchStrategy[T1],
    strategy2: SearchStrategy[T2],
    strategy3: SearchStrategy[T3],
    /,
) -> Callable[
    [Callable[[T1, T2, T3], Coroutine[None] | None]],
    Callable[[], Coroutine[None] | None],
]: ...
@overload
def test_hypothesis[T1, T2, T3, T4](
    strategy1: SearchStrategy[T1],
    strategy2: SearchStrategy[T2],
    strategy3: SearchStrategy[T3],
    strategy4: SearchStrategy[T4],
    /,
) -> Callable[
    [Callable[[T1, T2, T3, T4], Coroutine[None] | None]],
    Callable[[], Coroutine[None] | None],
]: ...
@overload
def test_hypothesis[T1, T2, T3, T4, T5](
    strategy1: SearchStrategy[T1],
    strategy2: SearchStrategy[T2],
    strategy3: SearchStrategy[T3],
    strategy4: SearchStrategy[T4],
    strategy5: SearchStrategy[T5],
    /,
) -> Callable[
    [Callable[[T1, T2, T3, T4, T5], Coroutine[None] | None]],
    Callable[[], Coroutine[None] | None],
]: ...
@overload
def test_hypothesis(
    *strategies: SearchStrategy[Any],
) -> Callable[
    [Callable[..., Coroutine[None] | None]],
    Callable[..., Coroutine[None] | None],
]: ...
