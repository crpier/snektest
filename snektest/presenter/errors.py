from typing import cast

from rich.console import Console

from snektest.models import (
    AssertionFailure,
    ErrorResult,
    FailedResult,
    TeardownFailure,
    TestResult,
)
from snektest.presenter.diff import render_assertion_failure
from snektest.presenter.traceback import render_traceback


def print_failures(  # noqa: PLR0912, C901
    console: Console,
    test_results: list[TestResult],
    session_teardown_failures: list[TeardownFailure] | None = None,
    session_teardown_output: str | None = None,
) -> None:
    """Print all test failures, fixture teardown failures, and session teardown failures."""
    if session_teardown_failures is None:
        session_teardown_failures = []

    failures = [
        result for result in test_results if isinstance(result.result, FailedResult)
    ]
    errors = [
        result for result in test_results if isinstance(result.result, ErrorResult)
    ]
    fixture_teardown_failures = [
        result for result in test_results if result.fixture_teardown_failures
    ]

    if (
        not failures
        and not errors
        and not fixture_teardown_failures
        and not session_teardown_failures
    ):
        return

    console.print()
    console.rule("[bold orange3]FAILURES", style="orange3", characters="=")
    console.print()

    # Print test failures
    for result in failures:
        console.rule(f"[bold red]{result.name}", style="red")
        failing_result = cast("FailedResult", result.result)

        render_traceback(
            console,
            failing_result.exc_type,
            failing_result.exc_value,
            failing_result.traceback,
        )

        if isinstance(failing_result.exc_value, AssertionFailure):
            render_assertion_failure(console, failing_result.exc_value)

        # Display captured output if any
        captured = result.captured_output.getvalue()
        if captured:
            console.print()
            console.print("[yellow]Captured output:[/yellow]")
            console.print(captured, markup=False, highlight=False)

        # Display fixture teardown output if any (shown when test fails)
        if result.fixture_teardown_output:
            console.print()
            console.print("[yellow]Captured output from fixture teardowns:[/yellow]")
            console.print(result.fixture_teardown_output, markup=False, highlight=False)

    # Print test errors
    for result in errors:
        console.rule(f"[bold dark_orange]{result.name}", style="dark_orange")
        error_result = cast("ErrorResult", result.result)

        render_traceback(
            console,
            error_result.exc_type,
            error_result.exc_value,
            error_result.traceback,
        )

        # Display captured output if any
        captured = result.captured_output.getvalue()
        if captured:
            console.print()
            console.print("[yellow]Captured output:[/yellow]")
            console.print(captured, markup=False, highlight=False)

        # Display fixture teardown output if any
        if result.fixture_teardown_output:
            console.print()
            console.print("[yellow]Captured output from fixture teardowns:[/yellow]")
            console.print(result.fixture_teardown_output, markup=False, highlight=False)

    # Print fixture teardown failures
    for result in fixture_teardown_failures:
        for teardown_failure in result.fixture_teardown_failures:
            console.rule(
                f"[bold red]{result.name} - Fixture teardown: {teardown_failure.fixture_name}",
                style="red",
            )
            render_traceback(
                console,
                teardown_failure.exc_type,
                teardown_failure.exc_value,
                teardown_failure.traceback,
            )

        # Display fixture teardown output if any (shown when fixture teardown fails)
        if result.fixture_teardown_output:
            console.print()
            console.print("[yellow]Captured output from fixture teardowns:[/yellow]")
            console.print(result.fixture_teardown_output, markup=False, highlight=False)

    # Print session teardown failures
    for teardown_failure in session_teardown_failures:
        console.rule(
            f"[bold red]Session fixture teardown: {teardown_failure.fixture_name}",
            style="red",
        )
        render_traceback(
            console,
            teardown_failure.exc_type,
            teardown_failure.exc_value,
            teardown_failure.traceback,
        )

    # Print session teardown output if there were test failures or errors
    if session_teardown_output and (failures or errors):
        console.print()
        console.rule(
            "[bold yellow]Output from session fixture teardowns",
            style="yellow",
        )
        console.print(session_teardown_output, markup=False, highlight=False)
