from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from snektest.models import (
    ErrorResult,
    FailedResult,
    PassedResult,
    TeardownFailure,
    TestResult,
)


@dataclass(frozen=True)
class RunCounts:
    passed: int
    failed: int
    errors: int
    fixture_teardown_failed: int
    session_teardown_failed: int


def _print_warnings(console: Console, test_results: list[TestResult]) -> None:
    """Print warnings from test results."""
    all_warnings = [w for result in test_results for w in result.warnings]
    if all_warnings:
        console.print()
        console.rule("[bold yellow]WARNINGS", style="yellow")
        for warning in all_warnings:
            console.print(f"[yellow]{warning}[/yellow]", markup=False)
        console.print()


def _print_test_failures(console: Console, test_results: list[TestResult]) -> None:
    """Print test failures."""
    for result in test_results:
        if (failed_result := result.result) and isinstance(failed_result, FailedResult):
            error_msg = str(failed_result.exc_value)
            console.print("FAILED", style="red", end=" ")
            if error_msg:
                console.print(f"{result.name} - {error_msg}", markup=False)
            else:
                console.print(f"{result.name}", markup=False)


def _print_test_errors(console: Console, test_results: list[TestResult]) -> None:
    """Print test errors (unexpected exceptions)."""
    for result in test_results:
        if (error_result := result.result) and isinstance(error_result, ErrorResult):
            error_msg = str(error_result.exc_value)
            console.print("ERROR", style="dark_orange", end=" ")
            if error_msg:
                console.print(f"{result.name} - {error_msg}", markup=False)
            else:
                console.print(f"{result.name}", markup=False)


def _print_fixture_teardown_failures(
    console: Console, test_results: list[TestResult]
) -> None:
    """Print fixture teardown failures."""
    for result in test_results:
        for teardown_failure in result.fixture_teardown_failures:
            console.print("FIXTURE TEARDOWN FAILED", style="red", end=" ")
            console.print(
                f"{result.name} - {teardown_failure.fixture_name}: {teardown_failure.exc_value}",
                markup=False,
            )


def _print_session_teardown_failures(
    console: Console, session_teardown_failures: list[TeardownFailure]
) -> None:
    """Print session teardown failures."""
    for teardown_failure in session_teardown_failures:
        console.print("SESSION FIXTURE TEARDOWN FAILED", style="red", end=" ")
        console.print(
            f"{teardown_failure.fixture_name}: {teardown_failure.exc_value}",
            markup=False,
        )


def _has_failures(counts: RunCounts) -> bool:
    return (
        counts.failed > 0
        or counts.errors > 0
        or counts.fixture_teardown_failed > 0
        or counts.session_teardown_failed > 0
    )


def _build_status_text(*, counts: RunCounts, total_duration: float) -> tuple[str, str]:
    """Build status text and set its color."""
    status_color = "red" if _has_failures(counts) else "green"
    status_text = f"[bold {status_color}]"
    if counts.failed > 0:
        status_text += f"{counts.failed} failed, "
    if counts.errors > 0:
        status_text += f"{counts.errors} error, "
    if counts.fixture_teardown_failed > 0:
        status_text += f"{counts.fixture_teardown_failed} fixture teardown failed, "
    if counts.session_teardown_failed > 0:
        status_text += (
            f"{counts.session_teardown_failed} session fixture teardown failed, "
        )
    status_text += (
        f"{counts.passed} passed in {total_duration:.2f}s[/bold {status_color}]"
    )
    return status_text, status_color


def print_summary(
    console: Console,
    test_results: list[TestResult],
    total_duration: float,
    session_teardown_failures: list[TeardownFailure] | None = None,
) -> None:
    """Print test summary with counts and status."""
    if session_teardown_failures is None:
        session_teardown_failures = []

    passed = sum(
        1 for result in test_results if isinstance(result.result, PassedResult)
    )
    failed = sum(
        1 for result in test_results if isinstance(result.result, FailedResult)
    )
    errors = sum(1 for result in test_results if isinstance(result.result, ErrorResult))
    fixture_teardown_failed = sum(
        len(result.fixture_teardown_failures) for result in test_results
    )
    session_teardown_failed = len(session_teardown_failures)

    counts = RunCounts(
        passed=passed,
        failed=failed,
        errors=errors,
        fixture_teardown_failed=fixture_teardown_failed,
        session_teardown_failed=session_teardown_failed,
    )

    _print_warnings(console, test_results)

    if _has_failures(counts):
        console.rule("[wheat1]SUMMARY", style="wheat1")
        _print_test_failures(console, test_results)
        _print_test_errors(console, test_results)
        _print_fixture_teardown_failures(console, test_results)
        _print_session_teardown_failures(console, session_teardown_failures)
        console.print()

    status_text, status_color = _build_status_text(
        counts=counts,
        total_duration=total_duration,
    )
    console.rule(status_text, style=status_color)
