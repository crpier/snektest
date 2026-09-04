"""Adapters for reporting test run progress and completion."""

from dataclasses import replace
from typing import Protocol

from snektest.models import PassedResult, RunResult, TestResult
from snektest.presenter import (
    print_failures,
    print_slowest_tests,
    print_summary,
    print_test_result,
)


class RunReporter(Protocol):
    """Interface for observing test execution without owning execution.

    `run_tests` owns execution, fixture teardown, and debugging. A reporter owns
    presentation side effects. Keeping that seam small lets command-line runs
    print progress while programmatic and JSON runs stay machine-readable.
    """

    retain_passed_output: bool
    """Whether completed passing results must keep their captured output."""

    def test_finished(self, test_result: TestResult) -> None:
        """Observe one completed test result before retention policy is applied."""

    def run_finished(self, run_result: RunResult) -> None:
        """Observe one normalized result after all fixture teardown."""


def result_for_retention(
    reporter: RunReporter,
    test_result: TestResult,
) -> TestResult:
    """Drop clean passing output after a reporter has consumed the full result."""
    if (
        reporter.retain_passed_output
        or not isinstance(test_result.result, PassedResult)
        or test_result.fixture_teardown_failures
    ):
        return test_result
    return replace(
        test_result,
        captured_output="",
        fixture_teardown_output=None,
    )


class ConsoleRunReporter:
    """Reporter adapter that renders the human-readable console output."""

    def __init__(
        self,
        *,
        durations: int | None = None,
        retain_passed_output: bool = False,
    ) -> None:
        self._durations: int | None = durations
        self.retain_passed_output: bool = retain_passed_output

    def test_finished(self, test_result: TestResult) -> None:
        print_test_result(test_result)

    def run_finished(self, run_result: RunResult) -> None:
        show_session_output = (
            run_result.failed > 0
            or run_result.errors > 0
            or run_result.fixture_teardown_failed > 0
            or run_result.session_teardown_failed > 0
        )
        print_failures(
            run_result.test_results,
            run_teardown_failures=run_result.run_teardown_failures,
            run_teardown_output=(
                run_result.run_teardown_output
                if run_result.run_teardown_failed > 0
                else None
            ),
            session_teardown_failures=run_result.session_teardown_failures,
            session_teardown_output=(
                run_result.session_teardown_output if show_session_output else None
            ),
        )
        if self._durations is not None:
            print_slowest_tests(run_result, count=self._durations)
        print_summary(run_result)


class NullRunReporter:
    """Reporter adapter for callers that need structured results only."""

    def __init__(self, *, retain_passed_output: bool = True) -> None:
        self.retain_passed_output: bool = retain_passed_output

    def test_finished(self, test_result: TestResult) -> None:
        _ = test_result

    def run_finished(self, run_result: RunResult) -> None:
        _ = run_result


class DeferredRunReporter:
    """Show test progress immediately but delay the final run summary."""

    def __init__(self, reporter: RunReporter) -> None:
        self._reporter: RunReporter = reporter
        self._run_finished: RunResult | None = None
        self.retain_passed_output: bool = reporter.retain_passed_output

    def test_finished(self, test_result: TestResult) -> None:
        self._reporter.test_finished(test_result)

    def run_finished(self, run_result: RunResult) -> None:
        self._run_finished = run_result

    def finish(self) -> None:
        """Emit the final summary after any post-run persistence succeeds."""
        run_finished = self._run_finished
        if run_finished is None:
            return
        self._reporter.run_finished(run_finished)


__all__ = [
    "ConsoleRunReporter",
    "DeferredRunReporter",
    "NullRunReporter",
    "RunReporter",
]
