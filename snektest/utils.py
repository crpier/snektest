"""Metadata helpers for decorated test functions."""

from collections.abc import Callable
from typing import Any, cast

from snektest.models import Param

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

PARAMS_ATTR_NAME = "__snektest_params__"
MARKERS_ATTR_NAME = "__snektest_markers__"
MUTEX_ATTR_NAME = "__snektest_mutex__"
XFAIL_ATTR_NAME = "__snektest_xfail__"


def mark_test_function(
    func: Callable[..., Any],
    params: tuple[list[Param[Any]], ...],
    markers: tuple[str, ...],
    mutex: str | None = None,
    expected_failure_reason: str | None = None,
) -> None:
    """Mark a function as a test and store its parameters."""
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))
    setattr(func, MARKERS_ATTR_NAME, markers)
    setattr(func, MUTEX_ATTR_NAME, mutex)
    setattr(func, XFAIL_ATTR_NAME, expected_failure_reason)


def is_test_function(func: Callable[..., Any]) -> bool:
    """Check if a function is marked as a test."""
    return getattr(func, TEST_ATTR_NAME, None) is TEST_ATTR_VALUE


def get_test_function_params(
    func: Callable[..., Any],
) -> dict[str, tuple[Param[Any], ...]]:
    """Get the parameters dict for a test function."""
    return getattr(func, PARAMS_ATTR_NAME)


def get_test_function_markers(func: Callable[..., Any]) -> tuple[str, ...]:
    """Get the markers tuple for a test function."""
    return cast("tuple[str, ...]", getattr(func, MARKERS_ATTR_NAME, ()))


def get_test_function_mutex(func: Callable[..., Any]) -> str | None:
    """Return the validated command-local mutex attached to a test function."""
    return cast("str | None", getattr(func, MUTEX_ATTR_NAME, None))


def get_test_function_xfail(func: Callable[..., Any]) -> str | None:
    """Return the expected-failure reason attached to a test function."""
    return cast("str | None", getattr(func, XFAIL_ATTR_NAME, None))
