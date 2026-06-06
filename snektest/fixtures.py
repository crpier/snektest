import asyncio
from collections.abc import AsyncGenerator, Awaitable, Generator
from dataclasses import dataclass
from inspect import isasyncgen, isgenerator
from types import CodeType
from typing import Any, cast

from snektest.annotations import Coroutine
from snektest.models import UnreachableError
from snektest.utils import get_code_from_generator, get_func_name_from_generator

_SESSION_FIXTURES: dict[
    CodeType, tuple[AsyncGenerator[Any] | Generator[Any] | None, object]
] = {}
_FUNCTION_FIXTURES: list[AsyncGenerator[Any] | Generator[Any]] = []


@dataclass(frozen=True)
class _PendingAsyncSessionFixtureSetup:
    """Shared async fixture setup while the first load is still pending."""

    awaitable: Awaitable[Any]


def _wrap_async_session_fixture_result[R](result: R) -> Coroutine[R]:
    async def wrapper() -> R:
        return result

    return wrapper()


def _create_async_session_fixture_setup[R](
    fixture_code: CodeType,
    gen: AsyncGenerator[R],
) -> Coroutine[R]:
    async def result_updater() -> R:
        registered_gen, _ = _SESSION_FIXTURES[fixture_code]
        if not isasyncgen(registered_gen):
            msg = "This should not happen I think"
            raise UnreachableError(msg)
        result = await anext(registered_gen)
        _SESSION_FIXTURES[fixture_code] = (registered_gen, result)
        return result

    awaitable: Awaitable[R]
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        awaitable = result_updater()
    else:
        awaitable = loop.create_task(result_updater())
    _SESSION_FIXTURES[fixture_code] = (
        gen,
        _PendingAsyncSessionFixtureSetup(awaitable),
    )
    return cast("Coroutine[R]", awaitable)


def register_session_fixture(
    fixture_code: CodeType,
) -> None:
    """Register a session-scoped fixture."""
    if fixture_code not in _SESSION_FIXTURES:
        _SESSION_FIXTURES[fixture_code] = (None, None)


def get_registered_session_fixtures() -> dict[
    CodeType, tuple[AsyncGenerator[Any] | Generator[Any] | None, object]
]:
    """Get all registered session fixtures."""
    return _SESSION_FIXTURES


def reset_session_fixtures() -> None:
    """Clear cached session fixtures for a fresh test run."""
    _SESSION_FIXTURES.clear()


def is_session_fixture(fixture_code: CodeType) -> bool:
    """Check if a fixture code object is registered as a session fixture."""
    return fixture_code in _SESSION_FIXTURES


def load_session_fixture[R](
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    """Load a session-scoped fixture, creating it on first use and reusing thereafter."""
    fixture_code = get_code_from_generator(fixture_gen)
    try:
        gen, cached_result = _SESSION_FIXTURES[fixture_code]
    except KeyError:
        msg = f"Function {fixture_code.co_qualname} was not registered as a session fixture. This shouldn't be possible!"
        raise UnreachableError(msg) from None

    if gen is None:
        gen = fixture_gen
        if isasyncgen(gen):
            return _create_async_session_fixture_setup(fixture_code, gen)
        if isgenerator(gen):
            cached_result = next(gen)
            _SESSION_FIXTURES[fixture_code] = (gen, cached_result)
            return cached_result
        msg = "Fixture must be a generator or async generator"
        raise UnreachableError(msg)

    if isinstance(cached_result, _PendingAsyncSessionFixtureSetup):
        return cast("Coroutine[R]", cached_result.awaitable)
    if isasyncgen(gen):
        return _wrap_async_session_fixture_result(cast("R", cached_result))
    return cast("R", cached_result)


def load_function_fixture[R](
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    """Load a function-scoped fixture by registering and yielding its value."""
    if isasyncgen(fixture_gen):
        _FUNCTION_FIXTURES.append(fixture_gen)
        return anext(fixture_gen)
    if isgenerator(fixture_gen):
        _FUNCTION_FIXTURES.append(fixture_gen)
        return next(fixture_gen)
    msg = "Fixture must be a generator or async generator"
    raise UnreachableError(msg)


def get_active_function_fixtures() -> list[
    tuple[str, AsyncGenerator[Any] | Generator[Any]]
]:
    """Return the list of active function fixtures, as (function_name, generator) tuples.

    Returns:
        List of (fixture_name, generator) tuples in reverse order.
    """
    fixtures_to_teardown: list[tuple[str, AsyncGenerator[Any] | Generator[Any]]] = []
    # Returning active fixtures in reverse order makes setup/teardown first-in-last-out
    for generator in reversed(_FUNCTION_FIXTURES):
        fixture_name = get_func_name_from_generator(generator)
        fixtures_to_teardown.append((fixture_name, generator))

    _FUNCTION_FIXTURES.clear()
    return fixtures_to_teardown
