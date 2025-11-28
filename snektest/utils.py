from collections.abc import AsyncGenerator, Callable
from types import CodeType
from typing import Any, cast

from snektest.models import Param

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

FIXTURE_ATTR_NAME = "__snektest_fixtures__"

PARAMS_ATTR_NAME = "__snektest_params__"

_SESSION_FIXTURES: dict[CodeType, tuple[AsyncGenerator[Any] | None, object]] = {}
_FUNCTION_FIXTURES: list[AsyncGenerator[Any]] = []


def mark_test_function(
    func: Callable[..., Any], params: tuple[list[Param[Any]], ...]
) -> None:
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))


def is_test_function(func: Callable[..., Any]) -> bool:
    return getattr(func, TEST_ATTR_NAME, None) is TEST_ATTR_VALUE


def get_test_function_params(
    func: Callable[..., Any],
) -> dict[tuple[str, ...], tuple[Param[Any], ...]]:
    return getattr(func, PARAMS_ATTR_NAME)


def register_session_fixture(
    fixture_code: CodeType,
) -> None:
    if fixture_code not in _SESSION_FIXTURES:
        _SESSION_FIXTURES[fixture_code] = (None, None)


def get_registered_session_fixtures() -> dict[
    CodeType, tuple[AsyncGenerator[Any] | None, object]
]:
    return _SESSION_FIXTURES


async def load_session_fixture[R](fixture_gen: AsyncGenerator[R]) -> R:
    fixture_code = cast("CodeType", fixture_gen.ag_code)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        gen, result = _SESSION_FIXTURES[fixture_code]
        if gen is None:
            gen = fixture_gen
            result = await anext(gen)
            _SESSION_FIXTURES[fixture_code] = (gen, result)
    except IndexError:
        msg = f"Function {fixture_code.__qualname__} was not registered as a session fixture"
        raise RuntimeError(msg) from None
    else:
        return result  # pyright: ignore[reportReturnType]
