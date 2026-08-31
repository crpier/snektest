from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from rich.console import Console
from rich.text import Text

from snektest.models import (
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
    console.print(title, style="yellow", markup=False)
    console.print(output, markup=False, highlight=False)


def _print_result_heading(
    console: Console,
    *,
    result: TestResult,
    status: str,
    style: str,
) -> None:
    """Print test names like progress output so they remain copy-pasteable."""
    console.print(
        Text.assemble(
            str(result.name),
            " ... ",
            (f"{status} ({result.duration:.2f}s)", style),
        ),
        markup=False,
        highlight=False,
        soft_wrap=True,
    )


def _print_fixture_teardown_heading(
    console: Console,
    *,
    result: TestResult,
    teardown_failure: TeardownFailure,
) -> None:
    """Print fixture failure headings without Rich rule title truncation."""
    console.print(
        Text.assemble(
            str(result.name),
            " ... ",
            (
                f"FIXTURE TEARDOWN FAILED: {teardown_failure.fixture_name}",
                "bold red",
            ),
        ),
        markup=False,
        highlight=False,
        soft_wrap=True,
    )


def _print_result_details(
    console: Console,
    *,
    result: TestResult,
    outcome: FailedResult | ErrorResult,
) -> None:
    exception = outcome.exception
    if exception.assertion is not None:
        render_traceback(
            console,
            exception,
            show_exception_line=False,
        )
        render_assertion_failure(console, exception.assertion)
    else:
        render_traceback(console, exception)

    _print_optional_output(
        console,
        title="Captured output:",
        output=result.captured_output,
    )
    _print_optional_output(
        console,
        title="Captured output from fixture teardowns:",
        output=result.fixture_teardown_output,
    )


def _print_test_failures(console: Console, failures: list[TestResult]) -> None:
    for result in failures:
        _print_result_heading(
            console,
            result=result,
            status="FAIL",
            style="bold red",
        )
        failing_result = cast("FailedResult", result.result)

        _print_result_details(
            console,
            result=result,
            outcome=failing_result,
        )
        console.print()


def _print_test_errors(console: Console, errors: list[TestResult]) -> None:
    for result in errors:
        _print_result_heading(
            console,
            result=result,
            status="ERROR",
            style="bold dark_orange",
        )
        error_result = cast("ErrorResult", result.result)

        _print_result_details(
            console,
            result=result,
            outcome=error_result,
        )
        console.print()


def _print_fixture_teardown_failures(
    console: Console, fixture_teardown_failures: list[TestResult]
) -> None:
    for result in fixture_teardown_failures:
        for teardown_failure in result.fixture_teardown_failures:
            _print_fixture_teardown_heading(
                console,
                result=result,
                teardown_failure=teardown_failure,
            )
            render_traceback(
                console,
                teardown_failure.exception,
            )
            console.print()

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
            f"Session fixture teardown: {teardown_failure.fixture_name}",
            style="bold red",
        )
        render_traceback(
            console,
            teardown_failure.exception,
        )
        console.print()


def _print_run_teardown_failures(
    console: Console, run_teardown_failures: list[TeardownFailure]
) -> None:
    for teardown_failure in run_teardown_failures:
        console.rule(
            f"Run fixture teardown: {teardown_failure.fixture_name}",
            style="bold red",
        )
        render_traceback(console, teardown_failure.exception)
        console.print()


def print_failures(  # noqa: PLR0913
    console: Console,
    test_results: list[TestResult],
    run_teardown_failures: list[TeardownFailure] | None = None,
    run_teardown_output: str | None = None,
    session_teardown_failures: list[TeardownFailure] | None = None,
    session_teardown_output: str | None = None,
) -> None:
    """Print all test failures, fixture teardown failures, and session teardown failures."""
    if session_teardown_failures is None:
        session_teardown_failures = []
    if run_teardown_failures is None:
        run_teardown_failures = []

    groups = _collect_failure_groups(test_results)

    if (
        not groups.failures
        and not groups.errors
        and not groups.fixture_teardown_failures
        and not run_teardown_failures
        and not session_teardown_failures
    ):
        return

    console.print()
    console.rule("FAILURES", style="bold orange3", characters="=")
    console.print()

    _print_test_failures(console, groups.failures)
    _print_test_errors(console, groups.errors)
    _print_fixture_teardown_failures(console, groups.fixture_teardown_failures)
    _print_session_teardown_failures(console, session_teardown_failures)
    _print_run_teardown_failures(console, run_teardown_failures)

    if session_teardown_output and (groups.failures or groups.errors):
        console.print()
        console.rule(
            "Output from session fixture teardowns",
            style="bold yellow",
        )
        console.print(session_teardown_output, markup=False, highlight=False)
    if run_teardown_output and (
        groups.failures or groups.errors or run_teardown_failures
    ):
        console.print()
        console.rule("Output from run fixture lifecycle", style="bold yellow")
        console.print(run_teardown_output, markup=False, highlight=False)
