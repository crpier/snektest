import difflib
import pprint
from typing import Any, cast

from rich.console import Console

from snektest.models import AssertionFailure


def render_assertion_failure(console: Console, exc: AssertionFailure) -> None:
    """Pretty-print an AssertionFailure using Rich, styled like pytest."""
    actual = exc.actual
    expected = exc.expected

    console.print(f"[red]E       {exc.args[0]}[/red]")

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
        return (
            f"[red]E       Left contains {actual_len - expected_len} more items[/red]"
        )
    return f"[red]E       Right contains {expected_len - actual_len} more items[/red]"


def _print_ndiff(
    console: Console, expected_lines: list[str], actual_lines: list[str]
) -> None:
    style_by_prefix: dict[str, str] = {
        "- ": "red",
        "+ ": "green",
        "? ": "dim red",
        "  ": "red",
    }

    for line in difflib.ndiff(expected_lines, actual_lines):
        prefix = line[:2]
        style = style_by_prefix.get(prefix)
        if style is None:
            continue
        console.print(f"[{style}]E       {line}[/{style}]")


def render_list_diff(console: Console, actual: list[Any], expected: list[Any]) -> None:
    """Render a pytest-like diff for lists."""
    console.print()

    diff_idx = _first_diff_index(actual, expected)
    if diff_idx is not None:
        console.print(
            f"[red]E       At index {diff_idx} diff: {actual[diff_idx]!r} != {expected[diff_idx]!r}[/red]"
        )
    else:
        msg = _length_mismatch_message(len(actual), len(expected))
        if msg is not None:
            console.print(msg)

    console.print("[red]E       [/red]")

    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()
    _print_ndiff(console, expected_lines, actual_lines)


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
