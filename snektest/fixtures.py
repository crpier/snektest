from collections.abc import AsyncGenerator, Generator
from inspect import isasyncgen, isgenerator
from types import CodeType
from typing import Any

from snektest.annotations import Coroutine
from snektest.models import UnreachableError
from snektest.utils import get_code_from_generator, get_func_name_from_generator

_SESSION_FIXTURES: dict[
    CodeType, tuple[AsyncGenerator[Any] | Generator[Any] | None, object]
] = {}
_FUNCTION_FIXTURES: list[AsyncGenerator[Any] | Generator[Any]] = []


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


def load_session_fixture[R](fixture_gen: AsyncGenerator[R] | Generator[R]) -> R:
    """Load a session-scoped fixture, creating it on first use and reusing thereafter."""
    fixture_code = get_code_from_generator(fixture_gen)
    try:
        gen, result = _SESSION_FIXTURES[fixture_code]
        if gen is None:
            gen = fixture_gen
            if isasyncgen(gen):

                async def result_updater() -> R:
                    gen, _ = _SESSION_FIXTURES[fixture_code]
                    if not isasyncgen(gen):
                        msg = "This should not happen I think"
                        raise UnreachableError(msg)
                    result = await anext(gen)

                    async def async_wrapper() -> R:
                        return result

                    _SESSION_FIXTURES[fixture_code] = (gen, async_wrapper())
                    return result

                result = result_updater()
            elif isgenerator(gen):
                result = next(gen)
            _SESSION_FIXTURES[fixture_code] = (gen, result)
    except KeyError:
        msg = f"Function {fixture_code.co_qualname} was not registered as a session fixture. This shouldn't be possible!"
        raise UnreachableError(msg) from None
    else:
        return result  # pyright: ignore[reportReturnType]


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
