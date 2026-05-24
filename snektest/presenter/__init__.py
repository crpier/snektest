from rich.console import Console

from snektest.models import (
    ErrorResult,
    FailedResult,
    PassedResult,
    TeardownFailure,
    TestResult,
)
from snektest.presenter.errors import print_failures as _print_failures
from snektest.presenter.summary import print_summary as _print_summary

console = Console()


def print_error(exc: str) -> None:
    """Print an error message in red."""
    console.print(exc, markup=False, style="red")


def print_test_result_to_console(console: Console, result: TestResult) -> None:
    console.print(
        f"{result.name!s} ... ",
        end="",
        markup=False,
        highlight=False,
        soft_wrap=True,
    )
    match result.result:
        case PassedResult():
            console.print(
                f"OK ({result.duration:.2f}s)",
                highlight=False,
                style="green",
                markup=False,
                soft_wrap=True,
            )
        case FailedResult():
            console.print(
                f"FAIL ({result.duration:.2f}s)",
                highlight=False,
                style="red",
                markup=False,
                soft_wrap=True,
            )
        case ErrorResult():
            console.print(
                f"ERROR ({result.duration:.2f}s)",
                highlight=False,
                style="dark_orange",
                markup=False,
                soft_wrap=True,
            )


def print_test_result(result: TestResult) -> None:
    """Print the result of a single test."""
    print_test_result_to_console(console, result)


def print_failures(
    test_results: list[TestResult],
    session_teardown_failures: list[TeardownFailure] | None = None,
    session_teardown_output: str | None = None,
) -> None:
    """Print all failures."""
    _print_failures(
        console,
        test_results,
        session_teardown_failures=session_teardown_failures,
        session_teardown_output=session_teardown_output,
    )


def print_summary(
    test_results: list[TestResult],
    total_duration: float,
    session_teardown_failures: list[TeardownFailure] | None = None,
) -> None:
    """Print test summary."""
    _print_summary(
        console,
        test_results,
        total_duration,
        session_teardown_failures=session_teardown_failures,
    )


__all__ = [
    "console",
    "print_error",
    "print_failures",
    "print_summary",
    "print_test_result",
    "print_test_result_to_console",
]
