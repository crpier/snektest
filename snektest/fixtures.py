from collections.abc import AsyncGenerator, Generator
from inspect import isasyncgen, isgenerator
from types import CodeType
from typing import Any

from snektest.annotations import Coroutine
from snektest.models import UnreachableError

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


def is_session_fixture(fixture_code: CodeType) -> bool:
    """Check if a fixture code object is registered as a session fixture."""
    return fixture_code in _SESSION_FIXTURES


def load_session_fixture[R](fixture_gen: AsyncGenerator[R] | Generator[R]) -> R:  # noqa: C901
    """Load a session-scoped fixture, creating it on first use and reusing thereafter.

    Raises:
        UnreachableError: Error that theoretically can't be reached
    """
    if isasyncgen(fixture_gen):
        fixture_code = fixture_gen.ag_code
    elif isgenerator(fixture_gen):
        fixture_code = fixture_gen.gi_code
    else:
        msg = "I'm only doing this to please the type checker"
        raise UnreachableError(msg)
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

                    async def async_wrapper() -> R:  # noqa: RUF029
                        return result

                    _SESSION_FIXTURES[fixture_code] = (gen, async_wrapper())
                    return result

                result = result_updater()
            elif isgenerator(gen):
                result = next(gen)
            else:
                msg = "Ooof, why?"
                raise UnreachableError(msg)
            _SESSION_FIXTURES[fixture_code] = (gen, result)
    except IndexError:
        msg = f"Function {fixture_code.__qualname__} was not registered as a session fixture. This shouldn't be possible!"
        raise UnreachableError(msg) from None
    else:
        return result  # pyright: ignore[reportReturnType]


def load_function_fixture[R](
    fixture_gen: AsyncGenerator[R] | Generator[R],
) -> Coroutine[R] | R:
    """Load a function-scoped fixture by appending it to the fixtures list and yielding its value.

    Raises:
        UnreachableError: Error that theoretically can't be reached
    """
    _FUNCTION_FIXTURES.append(fixture_gen)
    if isasyncgen(fixture_gen):
        return anext(fixture_gen)
    if isgenerator(fixture_gen):
        return next(fixture_gen)
    msg = "Fixture must be a generator or async generator"
    raise UnreachableError(msg)


def get_active_function_fixtures() -> list[
    tuple[str, AsyncGenerator[Any] | Generator[Any]]
]:
    """Return the list of active function fixtures, as (function_name, generator) tuples.

    Returns:
        List of (fixture_name, generator) tuples in reverse order.

    Raises:
        UnreachableError: Error that theoretically can't be reached
    """
    fixtures_to_teardown: list[tuple[str, AsyncGenerator[Any] | Generator[Any]]] = []
    # Returning active fixtures in reverse order makes setup/teardown first-in-last-out
    for generator in reversed(_FUNCTION_FIXTURES):
        if isasyncgen(generator):
            fixture_name = generator.ag_code.co_name
        elif isgenerator(generator):
            fixture_name = generator.gi_code.co_name
        else:
            msg = "Is there no better way"
            raise UnreachableError(msg)
        fixtures_to_teardown.append((fixture_name, generator))

    _FUNCTION_FIXTURES.clear()
    return fixtures_to_teardown
