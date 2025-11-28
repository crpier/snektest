from collections.abc import AsyncGenerator, Callable
from collections.abc import Coroutine as _Coroutine
from types import CodeType
from typing import Any, cast, overload

from snektest.models import Param
from snektest.models import Scope as Scope
from snektest.utils import (
    _FUNCTION_FIXTURES,  # pyright: ignore[reportPrivateUsage]
    load_session_fixture,
    mark_test_function,
    register_session_fixture,
)
from snektest.utils import (
    _SESSION_FIXTURES as _SESSION_FIXTURES,  # pyright: ignore[reportPrivateUsage]
)
from snektest.utils import (
    get_registered_session_fixtures as get_registered_session_fixtures,
)

type Coroutine[T] = _Coroutine[None, None, T]


@overload
def test() -> Callable[
    [Callable[[], Coroutine[None]]], Callable[[], Coroutine[None]]
]: ...


@overload
def test[T](
    param: list[Param[T]],
) -> Callable[[Callable[[T], Coroutine[None]]], Callable[[T], Coroutine[None]]]: ...


@overload
def test[T1, T2](
    param1: list[Param[T1]],
    param2: list[Param[T2]],
) -> Callable[
    [Callable[[T1, T2], Coroutine[None]]],
    Callable[[T1, T2], Coroutine[None]],
]: ...


def test(  # pyright: ignore[reportInconsistentOverload]
    *params: list[Param[Any]],
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None]]],
    Callable[[*tuple[Any, ...]], Coroutine[None]],
]:
    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None]],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None]]:
        mark_test_function(test_func, params)
        return test_func

    return decorator


def session_fixture[R]() -> Callable[
    [Callable[[], AsyncGenerator[R]]], Callable[[], AsyncGenerator[R]]
]:
    def decorator(
        fixture_func: Callable[[], AsyncGenerator[R]],
    ) -> Callable[[], AsyncGenerator[R]]:
        register_session_fixture(fixture_func.__code__)
        return fixture_func

    return decorator


async def load_fixture[R](
    fixture_gen: AsyncGenerator[R],
) -> R:
    fixture_gen_code = cast("CodeType", fixture_gen.ag_code)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    if fixture_gen_code in get_registered_session_fixtures():
        return await load_session_fixture(fixture_gen)

    _FUNCTION_FIXTURES.append(fixture_gen)
    return await anext(fixture_gen)
