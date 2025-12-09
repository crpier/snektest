import difflib
import pathlib
import pprint
from types import TracebackType
from typing import Any, cast

from rich.console import Console
from rich.syntax import Syntax

import snektest
from snektest.models import AssertionFailure, FailedResult, PassedResult, TestResult

# Initialize console
console = Console()


def print_error(exc: str) -> None:
    console.print(exc, markup=False, style="red")


def _render_traceback_no_box(
    console: Console,
    exc_type: type[BaseException],
    exc_value: BaseException,
    traceback: object,
) -> None:
    """Render a traceback without a box, using Rich for syntax highlighting."""
    # Print "Traceback (most recent call last):"
    console.print("[bold]Traceback[/bold] [dim](most recent call last):[/dim]")

    # Walk through traceback frames
    tb = traceback
    snektest_path = str(snektest.__file__).rsplit("/", 1)[0]

    while tb:
        if not isinstance(tb, TracebackType):
            break

        frame = tb.tb_frame
        lineno = tb.tb_lineno
        filename = frame.f_code.co_filename
        name = frame.f_code.co_name

        # Skip snektest internal frames (like cli.py)
        if not filename.startswith(snektest_path) or filename.endswith(
            "/assertions.py"
        ):
            # Print file location
            console.print(
                f'  File "[cyan]{filename}[/cyan]", line {lineno}, in [yellow]{name}[/yellow]'
            )

            # Read and print the code line with syntax highlighting
            try:
                with pathlib.Path(filename).open(encoding="utf-8") as f:
                    lines = f.readlines()
                    if 0 <= lineno - 1 < len(lines):
                        code_line = lines[lineno - 1].rstrip()
                        syntax = Syntax(
                            code_line,
                            "python",
                            theme="ansi_dark",
                            line_numbers=False,
                            padding=(0, 0, 0, 4),
                        )
                        console.print(syntax)
            except (OSError, IndexError):
                pass

        tb = tb.tb_next

    # Print the exception line
    exc_name = exc_type.__name__
    exc_msg = str(exc_value)
    console.print(f"[red bold]{exc_name}[/red bold]: {exc_msg}")


def print_test_result(result: TestResult) -> None:
    console.print(
        f"{result.name!s} ... ", end="", markup=False, highlight=False, no_wrap=True
    )
    if isinstance(result.result, PassedResult):
        console.print(
            f"[green]OK[/green] ({result.duration:.2f}s)", highlight=False, no_wrap=True
        )
    else:
        console.print(
            f"[red]FAIL[/red] ({result.duration:.2f}s)", highlight=False, no_wrap=True
        )


def print_failures(test_results: list[TestResult]) -> None:
    failures = [
        result for result in test_results if isinstance(result.result, FailedResult)
    ]
    if not failures:
        return

    console.print()
    console.rule("[bold orange3]FAILURES", style="orange3", characters="=")
    console.print()

    for result in failures:
        console.rule(f"[bold red]{result.name}", style="red")
        # We already made sure to filter for failing results only
        failing_result = cast("FailedResult", result.result)

        # Render traceback without box
        _render_traceback_no_box(
            console,
            failing_result.exc_type,
            failing_result.exc_value,
            failing_result.traceback,
        )

        if isinstance(failing_result.exc_value, AssertionFailure):
            render_assertion_failure(failing_result.exc_value)


def print_summary(test_results: list[TestResult], total_duration: float) -> None:
    passed_count = sum(1 for _ in test_results if isinstance(_.result, PassedResult))
    failed_count = sum(1 for _ in test_results if isinstance(_.result, FailedResult))

    if failed_count > 0:
        console.rule("[wheat1]SUMMARY", style="wheat1")
        for result in test_results:
            if (failed_result := result.result) and isinstance(
                failed_result, FailedResult
            ):
                error_msg = str(failed_result.exc_value)
                console.print("FAILED", style="red", end=" ")
                if error_msg:
                    console.print(
                        f"{result.name} - {error_msg}",
                        markup=False,
                    )
                else:
                    console.print(
                        f"{result.name}",
                        markup=False,
                    )
        console.print()

    status_color = "red" if failed_count > 0 else "green"
    status_text = f"[bold {status_color}]"
    if failed_count > 0:
        status_text += f"{failed_count} failed, "
    status_text += (
        f"{passed_count} passed in {total_duration:.2f}s[/bold {status_color}]"
    )

    console.rule(status_text, style=status_color)


def render_assertion_failure(exc: AssertionFailure) -> None:
    """
    Pretty-print an AssertionFailure using Rich, styled like pytest.
    """
    actual = exc.actual
    expected = exc.expected
    operator = exc.operator or "=="

    # Print the basic assertion error (matches pytest's "E       AssertionError: ...")
    console.print(f"[red]E       AssertionError[/red]: {exc.args[0]}")

    # Handle different types with custom diff rendering
    if isinstance(actual, list) and isinstance(expected, list):
        # The things I do to make pyright happy
        actual = cast("list[Any]", actual)
        expected = cast("list[Any]", expected)
        _render_list_diff(actual, expected)
    elif isinstance(actual, dict) and isinstance(expected, dict):
        # The things I do to make pyright happy
        actual = cast("dict[Any, Any]", actual)
        expected = cast("dict[Any, Any]", expected)
        _render_dict_diff(actual, expected)
    elif (
        isinstance(actual, str)
        and isinstance(expected, str)
        and ("\n" in actual or "\n" in expected)
    ):
        _render_multiline_string_diff(actual, expected)
    else:
        # For simple types, just show the values
        _render_simple_diff(actual, expected, operator)


def _render_simple_diff(actual: Any, expected: Any, operator: str) -> None:
    """Render a simple diff for basic types."""
    console.print(f"[red]E       {actual!r} {operator} {expected!r}[/red]")


def _render_list_diff(actual: list[Any], expected: list[Any]) -> None:  # noqa: C901
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

    # Use pprint for nice formatting
    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()

    # Create unified diff with character-level markers
    diff = list(difflib.ndiff(expected_lines, actual_lines))

    for line in diff:
        if line.startswith("- "):
            console.print(f"[red]E       {line}[/red]")
        elif line.startswith("+ "):
            console.print(f"[green]E       {line}[/green]")
        elif line.startswith("? "):
            console.print(f"[dim red]E       {line}[/dim red]")
        elif line.startswith("  "):
            console.print(f"[red]E       {line}[/red]")


def _render_dict_diff(actual: dict[Any, Any], expected: dict[Any, Any]) -> None:
    """Render a pytest-like diff for dicts."""
    console.print()

    # Use pprint for nice formatting
    expected_lines = pprint.pformat(expected, width=80).splitlines()
    actual_lines = pprint.pformat(actual, width=80).splitlines()

    # Create unified diff
    diff = difflib.ndiff(expected_lines, actual_lines)

    for line in diff:
        if line.startswith("- "):
            console.print(f"[red]E       {line}[/red]")
        elif line.startswith("+ "):
            console.print(f"[green]E       {line}[/green]")
        elif line.startswith("? "):
            console.print(f"[yellow]E       {line}[/yellow]")
        elif line.startswith("  "):
            console.print(f"[red]E       {line}[/red]")


def _render_multiline_string_diff(actual: str, expected: str) -> None:
    """Colored diff output for multiline strings using difflib."""
    console.print()

    diff_lines = difflib.ndiff(expected.splitlines(), actual.splitlines())

    for line in diff_lines:
        if line.startswith("+ "):
            console.print(f"[green]E       {line}[/green]")
        elif line.startswith("- "):
            console.print(f"[red]E       {line}[/red]")
        elif line.startswith("? "):
            console.print(f"[yellow]E       {line}[/yellow]")
        else:
            console.print(f"E       {line}")
