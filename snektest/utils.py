from collections.abc import AsyncGenerator, Callable, Generator
from inspect import isasyncgen, isgenerator
from types import CodeType
from typing import Any

from snektest.models import Param, UnreachableError

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

FIXTURE_ATTR_NAME = "__snektest_fixtures__"

PARAMS_ATTR_NAME = "__snektest_params__"

_SESSION_FIXTURES: dict[
    CodeType, tuple[AsyncGenerator[Any] | Generator[Any] | None, object]
] = {}
_FUNCTION_FIXTURES: list[AsyncGenerator[Any] | Generator[Any]] = []

# TODO: the functions in this module should do more work, so that callers
# don't have to know the internal structure


def mark_test_function(
    func: Callable[..., Any], params: tuple[list[Param[Any]], ...]
) -> None:
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))


def is_test_function(func: Callable[..., Any]) -> bool:
    return getattr(func, TEST_ATTR_NAME, None) is TEST_ATTR_VALUE


def get_test_function_params(
    func: Callable[..., Any],
) -> dict[str, tuple[Param[Any], ...]]:
    return getattr(func, PARAMS_ATTR_NAME)


def register_session_fixture(
    fixture_code: CodeType,
) -> None:
    if fixture_code not in _SESSION_FIXTURES:
        _SESSION_FIXTURES[fixture_code] = (None, None)


def get_registered_session_fixtures() -> dict[
    CodeType, tuple[AsyncGenerator[Any] | Generator[Any] | None, object]
]:
    return _SESSION_FIXTURES


def load_session_fixture[R](fixture_gen: AsyncGenerator[R] | Generator[R]) -> R:
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
                    result = await anext(gen)

                    async def async_wrapper():
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
