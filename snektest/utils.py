from collections.abc import Callable
from typing import Any

from snektest.models import Param

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

PARAMS_ATTR_NAME = "__snektest_params__"


def mark_test_function(
    func: Callable[..., Any], params: tuple[list[Param[Any]], ...]
) -> None:
    """Mark a function as a test and store its parameters."""
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))


def is_test_function(func: Callable[..., Any]) -> bool:
    """Check if a function is marked as a test."""
    return getattr(func, TEST_ATTR_NAME, None) is TEST_ATTR_VALUE


def get_test_function_params(
    func: Callable[..., Any],
) -> dict[str, tuple[Param[Any], ...]]:
    """Get the parameters dict for a test function."""
    return getattr(func, PARAMS_ATTR_NAME)
