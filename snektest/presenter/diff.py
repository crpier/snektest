import difflib
import pprint
from typing import Any, cast

from rich.console import Console

from snektest.models import AssertionFailure


def render_assertion_failure(console: Console, exc: AssertionFailure) -> None:
    """Pretty-print an AssertionFailure using Rich, styled like pytest."""
    actual = exc.actual
    expected = exc.expected
    operator = exc.operator or "=="

    console.print(f"[red]E       AssertionError[/red]: {exc.args[0]}")

    # Handle different types with custom diff rendering
    if isinstance(actual, list) and isinstance(expected, list):
        # I'm just casting list[Unknown] to list[Any] here to please our strict type check rules
        actual = cast("list[Any]", actual)
        expected = cast("list[Any]", expected)
        render_list_diff(console, actual, expected)
    elif isinstance(actual, dict) and isinstance(expected, dict):
        # I'm just casting dict[Unknown] to list[Any] here to please our strict type check rules
        actual = cast("dict[Any, Any]", actual)
        expected = cast("dict[Any, Any]", expected)
        render_dict_diff(console, actual, expected)
    elif (
        isinstance(actual, str)
        and isinstance(expected, str)
        and ("\n" in actual or "\n" in expected)
    ):
        render_multiline_string_diff(console, actual, expected)
    else:
        render_simple_diff(console, actual, expected, operator)


def render_simple_diff(
    console: Console, actual: Any, expected: Any, operator: str
) -> None:
    """Render a simple diff for basic types."""
    console.print(f"[red]E       {actual!r} {operator} {expected!r}[/red]")


def render_list_diff(console: Console, actual: list[Any], expected: list[Any]) -> None:  # noqa: C901
    """Render a pytest-like diff for lists."""
    console.print()

    # Find first difference
    diff_idx = None
    for i, (a, e) in enumerate(zip(actual, expected, strict=False)):
        if a != e:
            diff_idx = i
            break

    # Show index-level diff if items differ
    if diff_idx is not None:
        console.print(
            f"[red]E       At index {diff_idx} diff: {actual[diff_idx]!r} != {expected[diff_idx]!r}[/red]"
        )
    elif len(actual) != len(expected):
        # Length mismatch
        if len(actual) > len(expected):
            console.print(
                f"[red]E       Left contains {len(actual) - len(expected)} more items[/red]"
            )
        else:
            console.print(
                f"[red]E       Right contains {len(expected) - len(actual)} more items[/red]"
            )

    # Show full diff with +/- markers
    console.print("[red]E       [/red]")

    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()

    diff = list(difflib.ndiff(expected_lines, actual_lines))

    for line in diff:
        match line[:2]:
            case "- ":
                console.print(f"[red]E       {line}[/red]")
            case "+ ":
                console.print(f"[green]E       {line}[/green]")
            case "? ":
                console.print(f"[dim red]E       {line}[/dim red]")
            case "  ":
                console.print(f"[red]E       {line}[/red]")
            case _:
                ...


def render_dict_diff(
    console: Console, actual: dict[Any, Any], expected: dict[Any, Any]
) -> None:
    """Render a pytest-like diff for dicts."""
    console.print()

    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()

    diff = difflib.ndiff(expected_lines, actual_lines)

    for line in diff:
        match line[:2]:
            case "- ":
                console.print(f"[red]E       {line}[/red]")
            case "+ ":
                console.print(f"[green]E       {line}[/green]")
            case "? ":
                console.print(f"[yellow]E       {line}[/yellow]")
            case "  ":
                console.print(f"[red]E       {line}[/red]")
            case _:
                ...


def render_multiline_string_diff(console: Console, actual: str, expected: str) -> None:
    """Colored diff output for multiline strings using difflib."""
    console.print()

    diff_lines = difflib.ndiff(expected.splitlines(), actual.splitlines())

    for line in diff_lines:
        match line[:2]:
            case "+ ":
                console.print(f"[green]E       {line}[/green]")
            case "- ":
                console.print(f"[red]E       {line}[/red]")
            case "? ":
                console.print(f"[yellow]E       {line}[/yellow]")
            case _:
                console.print(f"E       {line}")
