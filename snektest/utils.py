from collections.abc import AsyncGenerator, Callable
from typing import Any, cast

from snektest.models import Param, Scope

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

FIXTURE_ATTR_NAME = "__snektest_fixtures__"

PARAMS_ATTR_NAME = "__snektest_params__"


# TODO: prevent a function from being both a test and a fixture
def mark_test_function(
    func: Callable[..., Any], params: tuple[list[Param[Any]], ...]
) -> None:
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))


def register_fixture(
    func: Callable[..., Any],
    fixture: tuple[Callable[..., Any], Scope, AsyncGenerator[Any]],
) -> None:
    if not hasattr(func, FIXTURE_ATTR_NAME):
        setattr(func, FIXTURE_ATTR_NAME, {})
    fixture_register = cast(
        "dict[Callable[..., Any], tuple[Scope, AsyncGenerator[Any]]]",
        getattr(func, FIXTURE_ATTR_NAME),
    )
    # TODO: should we always ignore already registered fixtures?
    if fixture_register.get(fixture[0]) is None:
        fixture_register[fixture[0]] = (fixture[1], fixture[2])


def get_registered_fixtures(
    func: Callable[..., Any],
) -> dict[Callable[..., Any], tuple[Scope, AsyncGenerator[Any]]] | None:
    return getattr(func, FIXTURE_ATTR_NAME, None)


def is_test_function(func: Callable[..., Any]) -> bool:
    return getattr(func, TEST_ATTR_NAME, None) is TEST_ATTR_VALUE


def get_test_function_params(
    func: Callable[..., Any],
) -> dict[tuple[str, ...], tuple[Param[Any], ...]]:
    return getattr(func, PARAMS_ATTR_NAME)
