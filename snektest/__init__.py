from collections.abc import AsyncGenerator, Callable, Generator
from inspect import isasyncgen, isgenerator
from typing import Any, overload

from snektest.annotations import Coroutine
from snektest.assertions import (
    assert_eq as assert_eq,
    assert_eq as assert_eq,
    assert_false as assert_false,
    assert_ge as assert_ge,
    assert_gt as assert_gt,
    assert_in as assert_in,
    assert_is as assert_is,
    assert_is_none as assert_is_none,
    assert_is_not as assert_is_not,
    assert_is_not_none as assert_is_not_none,
    assert_isinstance as assert_isinstance,
    assert_le as assert_le,
    assert_len as assert_len,
    assert_lt as assert_lt,
    assert_ne as assert_ne,
    assert_ne as assert_ne,
    assert_not_in as assert_not_in,
    assert_not_isinstance as assert_not_isinstance,
    assert_raise as assert_raise,
    assert_true as assert_true,
)
from snektest.models import Param, UnreachableError
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


def test(  # pyright: ignore[reportInconsistentOverload]
    *params: list[Param[Any]],
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None] | None]],
    Callable[[*tuple[Any, ...]], Coroutine[None] | None],
]:
    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None] | None],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None] | None]:
        mark_test_function(test_func, params)
        return test_func

    return decorator


def session_fixture[T, R: AsyncGenerator[T] | Generator[T]]() -> Callable[  # pyright: ignore[reportGeneralTypeIssues]
    [Callable[[], R]], Callable[[], R]
]:
    def decorator(
        fixture_func: Callable[[], R],
    ) -> Callable[[], R]:
        register_session_fixture(fixture_func.__code__)
        return fixture_func

    return decorator


@overload
def load_fixture[R](
    fixture_gen: Generator[R],
) -> R: ...


@overload
def load_fixture[R](
    fixture_gen: AsyncGenerator[R],
) -> Coroutine[R]: ...


def load_fixture[R](  # noqa: RET503
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    if isasyncgen(fixture_gen):
        fixture_gen_code = fixture_gen.ag_code
    elif isgenerator(fixture_gen):
        fixture_gen_code = fixture_gen.gi_code
    else:
        msg = "Hmm..."
        raise UnreachableError(msg)

    if fixture_gen_code in get_registered_session_fixtures():
        return load_session_fixture(fixture_gen)

    _FUNCTION_FIXTURES.append(fixture_gen)
    if isasyncgen(fixture_gen):
        return anext(fixture_gen)
    if isgenerator(fixture_gen):
        return next(fixture_gen)
