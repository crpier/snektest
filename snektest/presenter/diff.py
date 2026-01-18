import difflib
import pprint
from collections.abc import Callable
from typing import Any, cast

from rich.console import Console

from snektest.models import AssertionFailure


def render_assertion_failure(
    console: Console,
    exc: AssertionFailure,
    *,
    ndiff_func: Callable[[list[str], list[str]], Any] = difflib.ndiff,
) -> None:
    """Pretty-print an AssertionFailure using Rich, styled like pytest."""
    actual = exc.actual
    expected = exc.expected

    console.print(f"E       {exc.args[0]}", style="red", markup=False)

    if isinstance(actual, list) and isinstance(expected, list):
        # I'm just casting list[Unknown] to list[Any] here to please our strict type check rules
        actual = cast("list[Any]", actual)
        expected = cast("list[Any]", expected)
        render_list_diff(console, actual, expected, ndiff_func=ndiff_func)
    elif isinstance(actual, dict) and isinstance(expected, dict):
        # I'm just casting dict[Unknown] to list[Any] here to please our strict type check rules
        actual = cast("dict[Any, Any]", actual)
        expected = cast("dict[Any, Any]", expected)
        render_dict_diff(console, actual, expected, ndiff_func=ndiff_func)
    elif (
        isinstance(actual, str)
        and isinstance(expected, str)
        and ("\n" in actual or "\n" in expected)
    ):
        render_multiline_string_diff(
            console,
            actual,
            expected,
            ndiff_func=ndiff_func,
        )
    else:
        return


def _first_diff_index(actual: list[Any], expected: list[Any]) -> int | None:
    for index, (actual_item, expected_item) in enumerate(
        zip(actual, expected, strict=False)
    ):
        if actual_item != expected_item:
            return index
    return None


def _length_mismatch_message(actual_len: int, expected_len: int) -> str | None:
    if actual_len == expected_len:
        return None
    if actual_len > expected_len:
        return f"E       Left contains {actual_len - expected_len} more items"
    return f"E       Right contains {expected_len - actual_len} more items"


def _print_ndiff(
    console: Console,
    expected_lines: list[str],
    actual_lines: list[str],
    *,
    ndiff_func: Callable[[list[str], list[str]], Any] = difflib.ndiff,
) -> None:
    style_by_prefix: dict[str, str] = {
        "- ": "red",
        "+ ": "green",
        "? ": "dim red",
        "  ": "red",
    }

    for line in ndiff_func(expected_lines, actual_lines):
        prefix = line[:2]
        style = style_by_prefix.get(prefix)
        if style is None:
            continue
        console.print(f"E       {line}", style=style, markup=False)


def render_list_diff(
    console: Console,
    actual: list[Any],
    expected: list[Any],
    *,
    ndiff_func: Callable[[list[str], list[str]], Any] = difflib.ndiff,
) -> None:
    """Render a pytest-like diff for lists."""
    console.print()

    diff_idx = _first_diff_index(actual, expected)
    if diff_idx is not None:
        console.print(
            f"E       At index {diff_idx} diff: {actual[diff_idx]!r} != {expected[diff_idx]!r}",
            style="red",
            markup=False,
        )
    else:
        msg = _length_mismatch_message(len(actual), len(expected))
        if msg is not None:
            console.print(msg, style="red", markup=False)

    console.print("E       ", style="red", markup=False)

    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()
    _print_ndiff(console, expected_lines, actual_lines, ndiff_func=ndiff_func)


def render_dict_diff(
    console: Console,
    actual: dict[Any, Any],
    expected: dict[Any, Any],
    *,
    ndiff_func: Callable[[list[str], list[str]], Any] = difflib.ndiff,
) -> None:
    """Render a pytest-like diff for dicts."""
    console.print()

    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()

    diff = ndiff_func(expected_lines, actual_lines)

    for line in diff:
        match line[:2]:
            case "- ":
                console.print(f"E       {line}", style="red", markup=False)
            case "+ ":
                console.print(f"E       {line}", style="green", markup=False)
            case "? ":
                console.print(f"E       {line}", style="yellow", markup=False)
            case "  ":
                console.print(f"E       {line}", style="red", markup=False)
            case _:
                ...


def render_multiline_string_diff(
    console: Console,
    actual: str,
    expected: str,
    *,
    ndiff_func: Callable[[list[str], list[str]], Any] = difflib.ndiff,
) -> None:
    """Colored diff output for multiline strings using difflib."""
    console.print()

    diff_lines = ndiff_func(expected.splitlines(), actual.splitlines())

    for line in diff_lines:
        match line[:2]:
            case "+ ":
                console.print(f"E       {line}", style="green", markup=False)
            case "- ":
                console.print(f"E       {line}", style="red", markup=False)
            case "? ":
                console.print(f"E       {line}", style="yellow", markup=False)
            case _:
                console.print(f"E       {line}", markup=False)
