from __future__ import annotations

from rich.console import Console
from rich.text import Text

from snektest.models import (
    ErrorResult,
    ExceptionDiagnostic,
    ExpectedFailureResult,
    FailedResult,
    RunResult,
    SkippedResult,
    TeardownFailure,
    TestResult,
    UnexpectedPassResult,
)


def _ellipsize_summary_detail(detail: str, max_width: int) -> str:
    """Shorten only summary diagnostics, never the test name."""
    if not detail or len(detail) <= max_width:
        return detail
    return f"{detail[: max_width - 1]}…"


def _print_summary_entry(
    console: Console,
    *,
    label: str,
    style: str,
    details: str,
) -> None:
    line = Text.assemble((label, style), " ", details)
    console.print(line, markup=False, soft_wrap=True)


def _print_named_summary_entry(
    console: Console,
    *,
    label: tuple[str, str],
    test_name: str,
    detail_prefix: str = "",
    detail: str = "",
) -> None:
    label_text, label_style = label
    available_detail_width = max(
        console.width - len(label_text) - 1 - len(detail_prefix), 1
    )
    line = Text.assemble(
        (label_text, label_style),
        " ",
        test_name,
        detail_prefix,
        _ellipsize_summary_detail(detail, available_detail_width),
    )
    console.print(line, markup=False, soft_wrap=True)


def _summarize_exception(exception: ExceptionDiagnostic) -> str:
    """Render exception values as single-line summary details.

    Full multi-line exception messages are already shown in the failure details.
    The summary keeps only the first line so repeated diagnostics do not swamp
    the final count line.
    """
    first_line = exception.message.splitlines()[0] if exception.message else ""
    if not first_line:
        return ""
    return f"{exception.type_name}: {first_line}"


def _print_warnings(console: Console, run_result: RunResult) -> None:
    """Print warnings retained across all phases of the run."""
    if run_result.warnings:
        console.print()
        console.rule("WARNINGS", style="bold yellow")
        for warning in run_result.warnings:
            console.print(warning, markup=False, style="yellow")
        console.print()


def _print_test_failures(console: Console, test_results: list[TestResult]) -> None:
    """Print test failures."""
    for result in test_results:
        if (failed_result := result.result) and isinstance(failed_result, FailedResult):
            error_msg = _summarize_exception(failed_result.exception)
            _print_named_summary_entry(
                console,
                label=("FAILED", "red"),
                test_name=str(result.name),
                detail_prefix=" - " if error_msg else "",
                detail=error_msg,
            )


def _print_test_errors(console: Console, test_results: list[TestResult]) -> None:
    """Print test errors (unexpected exceptions)."""
    for result in test_results:
        if (error_result := result.result) and isinstance(error_result, ErrorResult):
            error_msg = _summarize_exception(error_result.exception)
            _print_named_summary_entry(
                console,
                label=("ERROR", "dark_orange"),
                test_name=str(result.name),
                detail_prefix=" - " if error_msg else "",
                detail=error_msg,
            )


def _print_outcomes(console: Console, test_results: list[TestResult]) -> None:
    """List non-pass outcomes with the same labels used during progress."""
    for test_result in test_results:
        match test_result.result:
            case SkippedResult(reason=reason):
                label = ("SKIPPED", "cyan")
            case ExpectedFailureResult(reason=reason):
                label = ("XFAIL", "yellow")
            case UnexpectedPassResult(reason=reason):
                label = ("XPASS", "red")
            case _:
                continue
        _print_named_summary_entry(
            console,
            label=label,
            test_name=str(test_result.name),
            detail_prefix=" - ",
            detail=reason,
        )


def _print_fixture_teardown_failures(
    console: Console, test_results: list[TestResult]
) -> None:
    """Print fixture teardown failures."""
    for result in test_results:
        for teardown_failure in result.fixture_teardown_failures:
            error_msg = _summarize_exception(teardown_failure.exception)
            _print_named_summary_entry(
                console,
                label=("FIXTURE TEARDOWN FAILED", "red"),
                test_name=str(result.name),
                detail_prefix=f" - {teardown_failure.fixture_name}: ",
                detail=error_msg,
            )


def _print_session_teardown_failures(
    console: Console, session_teardown_failures: list[TeardownFailure]
) -> None:
    """Print session teardown failures."""
    for teardown_failure in session_teardown_failures:
        error_msg = _summarize_exception(teardown_failure.exception)
        _print_summary_entry(
            console,
            label="SESSION FIXTURE TEARDOWN FAILED",
            style="red",
            details=f"{teardown_failure.fixture_name}: {error_msg}",
        )


def _print_run_teardown_failures(
    console: Console, run_teardown_failures: list[TeardownFailure]
) -> None:
    for teardown_failure in run_teardown_failures:
        error_msg = _summarize_exception(teardown_failure.exception)
        _print_summary_entry(
            console,
            label="RUN FIXTURE TEARDOWN FAILED",
            style="red",
            details=f"{teardown_failure.fixture_name}: {error_msg}",
        )


def _has_failures(counts: RunResult) -> bool:
    return (
        counts.failed > 0
        or counts.errors > 0
        or counts.unexpected_passes > 0
        or counts.fixture_teardown_failed > 0
        or counts.run_teardown_failed > 0
        or counts.session_teardown_failed > 0
    )


def _has_summary_entries(counts: RunResult) -> bool:
    return _has_failures(counts) or counts.skipped > 0 or counts.expected_failures > 0


def _build_status_text(*, counts: RunResult) -> tuple[str, str]:
    """Build status text and set its color."""
    status_color = "red" if _has_failures(counts) else "green"
    status_text = ""
    if counts.failed > 0:
        status_text += f"{counts.failed} failed, "
    if counts.errors > 0:
        status_text += f"{counts.errors} error, "
    if counts.unexpected_passes > 0:
        status_text += f"{counts.unexpected_passes} unexpected pass, "
    if counts.skipped > 0:
        status_text += f"{counts.skipped} skipped, "
    if counts.expected_failures > 0:
        status_text += f"{counts.expected_failures} expected failure, "
    if counts.fixture_teardown_failed > 0:
        status_text += f"{counts.fixture_teardown_failed} fixture teardown failed, "
    if counts.session_teardown_failed > 0:
        status_text += (
            f"{counts.session_teardown_failed} session fixture teardown failed, "
        )
    if counts.run_teardown_failed > 0:
        status_text += f"{counts.run_teardown_failed} run fixture teardown failed, "
    status_text += f"{counts.passed} passed in {counts.total_duration:.2f}s"
    return status_text, f"bold {status_color}"


def print_slowest_tests(
    console: Console,
    run_result: RunResult,
    *,
    count: int,
) -> None:
    """Render completed tests from slowest to fastest with reusable selectors."""
    slowest_tests = sorted(
        run_result.test_results,
        key=lambda test_result: (-test_result.duration, test_result.ordinal),
    )[:count]
    if not slowest_tests:
        return
    console.rule("SLOWEST TESTS", style="blue")
    for test_result in slowest_tests:
        console.print(
            f"{test_result.duration:.2f}s selector: {test_result.name}",
            markup=False,
        )
    console.print()


def print_summary(console: Console, run_result: RunResult) -> None:
    """Render counts already normalized by the completed run."""
    _print_warnings(console, run_result)

    if _has_summary_entries(run_result):
        console.rule("SUMMARY", style="wheat1")
        _print_outcomes(console, run_result.test_results)
        _print_test_failures(console, run_result.test_results)
        _print_test_errors(console, run_result.test_results)
        _print_fixture_teardown_failures(console, run_result.test_results)
        _print_session_teardown_failures(console, run_result.session_teardown_failures)
        _print_run_teardown_failures(console, run_result.run_teardown_failures)
        console.print()

    if run_result.stopped_early:
        console.print(
            f"Stopped early: {run_result.total_tests} of "
            f"{run_result.selected_tests} tests ran",
            markup=False,
        )

    status_text, status_style = _build_status_text(counts=run_result)
    console.rule(status_text, style=status_style)
