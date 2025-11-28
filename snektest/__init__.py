from collections.abc import AsyncGenerator, Callable
from collections.abc import Coroutine as _Coroutine
from inspect import currentframe, getouterframes
from typing import Any, cast, overload

from snektest.models import Param
from snektest.models import Scope as Scope
from snektest.utils import (
    _SESSION_FIXTURES as _SESSION_FIXTURES,  # pyright: ignore[reportPrivateUsage]
)
from snektest.utils import (
    get_registered_session_fixtures as get_registered_session_fixtures,
)
from snektest.utils import (
    load_function_fixture,
    load_session_fixture,
    mark_test_function,
    register_session_fixture,
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
        register_session_fixture(fixture_func)
        return fixture_func

    return decorator


async def load_fixture[R](
    fixture_gen: AsyncGenerator[R],
) -> R:
    fixture_func = cast(
        "Callable[..., Any]",
        fixture_gen.ag_frame.f_globals[fixture_gen.ag_code.co_name],  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    )
    if fixture_func in get_registered_session_fixtures():
        return await load_session_fixture(fixture_func)
    frame = currentframe()
    outer_frames = getouterframes(frame)
    function_frame = outer_frames[1]
    test_func = cast(
        "Callable[..., Any]",
        # TODO: might be able to remove this horrible thing by storing code objects directly
        function_frame.frame.f_globals[function_frame.frame.f_code.co_name],
    )
    load_function_fixture(
        test_func=test_func, fixture_func=fixture_func, fixture_gen=fixture_gen
    )

    return await anext(fixture_gen)
