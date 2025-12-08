import difflib
from typing import cast

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.traceback import Traceback

import snektest
from snektest.models import AssertionFailure, FailedResult, PassedResult, TestResult

# Initialize console
console = Console()


def print_error(exc: str) -> None:
    console.print(exc, markup=False, style="red")


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
    console.rule("[bold red]FAILURES", style="red")
    console.print()

    for result in failures:
        console.rule(f"[bold red]{result.name}", characters="=", style="red")
        # We already made sure to filter for failing results only
        failing_result = cast("FailedResult", result.result)
        tb = Traceback.from_exception(
            failing_result.exc_type,
            failing_result.exc_value,
            failing_result.traceback,
            suppress=[snektest],
            max_frames=3
        )
        console.print(tb)
        # if isinstance(failing_result.exc_value, AssertionFailure):
        #     render_assertion_failure(failing_result.exc_value)


def print_summary(test_results: list[TestResult], total_duration: float) -> None:
    passed_count = sum(1 for _ in test_results if isinstance(_.result, PassedResult))
    failed_count = sum(1 for _ in test_results if isinstance(_.result, FailedResult))

    if failed_count > 0:
        console.rule("[wheat1]SUMMARY", style="wheat1")
        for result in test_results:
            if (failed_result := result.result) and isinstance(
                failed_result, FailedResult
            ):
                error_desc = str(failed_result.exc_type)
                if str(failed_result.exc_value) != "":
                    error_desc += f": {failed_result.exc_value}"
                console.print("FAILED", style="red", end=" ")
                console.print(
                    f"{result.name} - {error_desc}",
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
    Pretty-print an AssertionFailure using Rich.
    """

    # Build a simple Expected/Actual table
    table = Table(show_header=False, box=None)
    table.add_row("Expected:", repr(exc.expected))
    table.add_row("Actual:", repr(exc.actual))

    # Optional string diff
    diff_block = None
    if isinstance(exc.actual, str) and isinstance(exc.expected, str):
        diff_block = _render_string_diff(exc.actual, exc.expected)

    # Build a Rich group with components
    parts: list[Text | Table] = [table]
    if diff_block:
        parts.append(diff_block)

    # Show the exception message + failure panel
    content = Panel(
        Text(exc.args[0], style="red") if exc.args else table,
        title="[bold red]Assertion Failed[/bold red]",
        border_style="red",
    )

    console.print(content)

    # print the details (Expected/Actual + diff)
    for part in parts:
        console.print(part)


def _render_string_diff(a: str, b: str) -> Text:
    """
    Colored diff output for strings using difflib.
    """

    diff_lines = difflib.ndiff(b.splitlines(), a.splitlines())
    text = Text()

    for line in diff_lines:
        if line.startswith("+"):
            _ = text.append(line + "\n", style="green")
        elif line.startswith("-"):
            _ = text.append(line + "\n", style="red")
        elif line.startswith("?"):
            _ = text.append(line + "\n", style="yellow")
        else:
            _ = text.append(line + "\n")

    return text
