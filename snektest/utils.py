from collections import defaultdict
from collections.abc import AsyncGenerator, Callable
from typing import Any, cast

from snektest.models import Param

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

FIXTURE_ATTR_NAME = "__snektest_fixtures__"

PARAMS_ATTR_NAME = "__snektest_params__"

_SESSION_FIXTURES: dict[
    Callable[[], AsyncGenerator[Any]], tuple[AsyncGenerator[Any] | None, object]
] = {}


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


# TODO: unify these 2 functions into 1, based on given scope...or maybe not?
def load_function_fixture(
    test_func: Callable[..., Any],
    fixture_func: Callable[..., Any],
    fixture_gen: AsyncGenerator[Any],
) -> None:
    if not hasattr(test_func, FIXTURE_ATTR_NAME):
        setattr(test_func, FIXTURE_ATTR_NAME, defaultdict(list))
    fixture_register = cast(
        "dict[Callable[..., Any], list[AsyncGenerator[Any]]]",
        getattr(test_func, FIXTURE_ATTR_NAME),
    )
    fixture_register[fixture_func].append(fixture_gen)


def get_loaded_function_fixtures(
    func: Callable[..., Any],
) -> dict[Callable[..., Any], list[AsyncGenerator[Any]]]:
    return getattr(func, FIXTURE_ATTR_NAME, {})


def register_session_fixture(
    # TODO: reusable type for fixture func
    fixture_func: Callable[[], AsyncGenerator[Any]],
) -> None:
    if fixture_func not in _SESSION_FIXTURES:
        _SESSION_FIXTURES[fixture_func] = (None, None)


def get_registered_session_fixtures() -> dict[
    Callable[[], AsyncGenerator[Any]], tuple[AsyncGenerator[Any] | None, object]
]:
    return _SESSION_FIXTURES


async def load_session_fixture[R](fixture_func: Callable[[], AsyncGenerator[R]]) -> R:
    try:
        gen, result = _SESSION_FIXTURES[fixture_func]
        if gen is None:
            gen = fixture_func()
            result = await anext(gen)
            _SESSION_FIXTURES[fixture_func] = (gen, result)
    except IndexError:
        msg = f"Function {fixture_func} was not registered as a fixture"
        raise RuntimeError(msg) from None
    else:
        return result  # pyright: ignore[reportReturnType]
