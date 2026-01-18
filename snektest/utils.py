from collections.abc import AsyncGenerator, Callable, Generator
from inspect import isasyncgen
from types import CodeType
from typing import Any

from snektest.models import Param

TEST_ATTR_NAME = "__is_snektest__test__"
TEST_ATTR_VALUE = object()

PARAMS_ATTR_NAME = "__snektest_params__"
MARKERS_ATTR_NAME = "__snektest_markers__"


def mark_test_function(
    func: Callable[..., Any],
    params: tuple[list[Param[Any]], ...],
    markers: tuple[str, ...],
) -> None:
    """Mark a function as a test and store its parameters."""
    setattr(func, TEST_ATTR_NAME, TEST_ATTR_VALUE)
    setattr(func, PARAMS_ATTR_NAME, Param.to_dict(params))
    setattr(func, MARKERS_ATTR_NAME, markers)


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
    return getattr(func, MARKERS_ATTR_NAME, ())


def get_code_from_generator(
    generator: AsyncGenerator[Any] | Generator[Any],
) -> CodeType:
    """Get the code object from a generator."""
    return generator.ag_code if isasyncgen(generator) else generator.gi_code  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]


def get_func_name_from_generator(
    generator: AsyncGenerator[Any] | Generator[Any],
) -> str:
    """Get the code object from a generator."""
    return (
        generator.ag_code.co_name
        if isasyncgen(generator)
        else generator.gi_code.co_name  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
    )
