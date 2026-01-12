from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class FailureGroups:
    failures: list[TestResult]
    errors: list[TestResult]
    fixture_teardown_failures: list[TestResult]


def _collect_failure_groups(test_results: list[TestResult]) -> FailureGroups:
    failures = [
        result for result in test_results if isinstance(result.result, FailedResult)
    ]
    errors = [
        result for result in test_results if isinstance(result.result, ErrorResult)
    ]
    fixture_teardown_failures = [
        result for result in test_results if result.fixture_teardown_failures
    ]
    return FailureGroups(
        failures=failures,
        errors=errors,
        fixture_teardown_failures=fixture_teardown_failures,
    )


def _print_optional_output(console: Console, *, title: str, output: str | None) -> None:
    if not output:
        return
    console.print()
    console.print(f"[yellow]{title}[/yellow]")
    console.print(output, markup=False, highlight=False)


def _print_result_details(
    console: Console,
    *,
    result: TestResult,
    exc_type: type[BaseException],
    exc_value: BaseException,
    traceback: object,
) -> None:
    render_traceback(console, exc_type, exc_value, traceback)

    if isinstance(exc_value, AssertionFailure):
        render_assertion_failure(console, exc_value)

    _print_optional_output(
        console,
        title="Captured output:",
        output=result.captured_output.getvalue(),
    )
    _print_optional_output(
        console,
        title="Captured output from fixture teardowns:",
        output=result.fixture_teardown_output,
    )


def _print_test_failures(console: Console, failures: list[TestResult]) -> None:
    for result in failures:
        console.rule(f"[bold red]{result.name}", style="red")
        failing_result = cast("FailedResult", result.result)

        _print_result_details(
            console,
            result=result,
            exc_type=failing_result.exc_type,
            exc_value=failing_result.exc_value,
            traceback=failing_result.traceback,
        )


def _print_test_errors(console: Console, errors: list[TestResult]) -> None:
    for result in errors:
        console.rule(f"[bold dark_orange]{result.name}", style="dark_orange")
        error_result = cast("ErrorResult", result.result)

        _print_result_details(
            console,
            result=result,
            exc_type=error_result.exc_type,
            exc_value=error_result.exc_value,
            traceback=error_result.traceback,
        )


def _print_fixture_teardown_failures(
    console: Console, fixture_teardown_failures: list[TestResult]
) -> None:
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

        _print_optional_output(
            console,
            title="Captured output from fixture teardowns:",
            output=result.fixture_teardown_output,
        )


def _print_session_teardown_failures(
    console: Console, session_teardown_failures: list[TeardownFailure]
) -> None:
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


def print_failures(
    console: Console,
    test_results: list[TestResult],
    session_teardown_failures: list[TeardownFailure] | None = None,
    session_teardown_output: str | None = None,
) -> None:
    """Print all test failures, fixture teardown failures, and session teardown failures."""
    if session_teardown_failures is None:
        session_teardown_failures = []

    groups = _collect_failure_groups(test_results)

    if (
        not groups.failures
        and not groups.errors
        and not groups.fixture_teardown_failures
        and not session_teardown_failures
    ):
        return

    console.print()
    console.rule("[bold orange3]FAILURES", style="orange3", characters="=")
    console.print()

    _print_test_failures(console, groups.failures)
    _print_test_errors(console, groups.errors)
    _print_fixture_teardown_failures(console, groups.fixture_teardown_failures)
    _print_session_teardown_failures(console, session_teardown_failures)

    if session_teardown_output and (groups.failures or groups.errors):
        console.print()
        console.rule(
            "[bold yellow]Output from session fixture teardowns",
            style="yellow",
        )
        console.print(session_teardown_output, markup=False, highlight=False)
