from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any

from snektest.annotations import Coroutine
from snektest.fixtures import (
    is_session_fixture,
    load_function_fixture,
    load_session_fixture,
    register_session_fixture,
)
from snektest.models import Param
from snektest.utils import get_code_from_generator, mark_test_function


def test(
    *params: list[Param[Any]],
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None] | None]],
    Callable[[*tuple[Any, ...]], Coroutine[None] | None],
]:
    """Mark a function as a test function."""

    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None] | None],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None] | None]:
        mark_test_function(test_func, params)
        return test_func

    return decorator


def session_fixture[T, R: AsyncGenerator[T] | Generator[T]]() -> Callable[  # pyright: ignore[reportGeneralTypeIssues]
    [Callable[[], R]], Callable[[], R]
]:
    """Mark a function as a session fixture. Unlike regular fixtures,
    session fixtures are loaded once per test session, not once per test
    function.
    Loading a session fixture multiple times returns the generate value from
    the first load."""

    def decorator(
        fixture_func: Callable[[], R],
    ) -> Callable[[], R]:
        register_session_fixture(fixture_func.__code__)
        return fixture_func

    return decorator


def load_fixture[R](
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    """Load a fixture from a generator.
    When loading a fixture, `snektest` takes care to handle tearing down the
    fixture after the test has finished."""
    fixture_gen_code = get_code_from_generator(fixture_gen)

    if is_session_fixture(fixture_gen_code):
        return load_session_fixture(fixture_gen)

    return load_function_fixture(fixture_gen)
