from collections.abc import AsyncGenerator, Callable, Generator
from inspect import isasyncgen, isgenerator
from typing import Any

from snektest.annotations import Coroutine
from snektest.fixtures import (
    is_session_fixture,
    load_function_fixture,
    load_session_fixture,
    register_session_fixture,
)
from snektest.models import Param, UnreachableError
from snektest.utils import mark_test_function


def test(
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


def load_fixture[R](
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    if isasyncgen(fixture_gen):
        fixture_gen_code = fixture_gen.ag_code
    elif isgenerator(fixture_gen):
        fixture_gen_code = fixture_gen.gi_code
    else:
        msg = "Hmm..."
        raise UnreachableError(msg)

    if is_session_fixture(fixture_gen_code):
        return load_session_fixture(fixture_gen)

    return load_function_fixture(fixture_gen)
