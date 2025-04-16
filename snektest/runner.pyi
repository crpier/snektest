from collections.abc import AsyncGenerator as _AsyncGenerator
from collections.abc import Generator as _Generator
from typing import (
    Awaitable,
    Callable,
    Literal,
    ParamSpec,
    TypeVar,
    TypeVarTuple,
    Unpack,
)

FixtureScope = Literal["test", "session"]
T = TypeVar("T")
T2 = TypeVarTuple("T2")
P = ParamSpec("P")
Generator = _Generator[T, None, None]
AsyncGenerator = _AsyncGenerator[T, None]

def fixture_async(
    *params: Unpack[T2], scope: FixtureScope = "test"
) -> Callable[
    [Callable[[Unpack[T2]], AsyncGenerator[T]]],
    Callable[[Unpack[T2]], AsyncGenerator[T]],
]: ...
async def load_fixture_async(fixture: Callable[..., AsyncGenerator[T]]) -> T: ...
def test_async(
    *params: Unpack[T2],
) -> Callable[
    [Callable[[Unpack[T2]], Awaitable[None]]], Callable[[Unpack[T2]], None]
]: ...
