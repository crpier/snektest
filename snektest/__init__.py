from collections.abc import Callable, Generator
from typing import (
    Literal,
    TypeVar,
    TypeVarTuple,
    Unpack,
)

from snektest.runner import global_session

T = TypeVar("T")
TT = TypeVarTuple("TT")

FixtureScope = Literal["test", "session"]


def test[*TT](
    *__params__: *TT,  # noqa: ARG001
) -> Callable[[Callable[[Unpack[TT]], None]], Callable[[Unpack[TT]], None]]:
    def decorator(test_func: Callable[..., None]) -> Callable[..., None]:
        global_session.register_test(test_func)
        return test_func

    return decorator


def fixture[*TT, T](
    *__params__: *TT,
    scope: FixtureScope = "test",
) -> Callable[
    [Callable[[Unpack[TT]], Generator[T] | T]],
    Callable[[Unpack[TT]], T],
]: ...


def load_fixture[T](fixture: Callable[..., Generator[T] | T]) -> T: ...
