"""Adapters for reporting test run progress and completion."""

from dataclasses import dataclass
from typing import Protocol

from snektest.models import TeardownFailure, TestResult
from snektest.presenter import print_failures, print_summary, print_test_result


class RunReporter(Protocol):
    """Interface for observing test execution without owning execution.

    `run_tests` owns execution, fixture teardown, and debugging. A reporter owns
    presentation side effects. Keeping that seam small lets command-line runs
    print progress while programmatic and JSON runs stay machine-readable.
    """

    def test_finished(self, test_result: TestResult) -> None:
        """Observe one completed test result."""

    def run_finished(
        self,
        *,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        """Observe final run results after session fixture teardown."""


class ConsoleRunReporter:
    """Reporter adapter that renders the human-readable console output."""

    def test_finished(self, test_result: TestResult) -> None:
        print_test_result(test_result)

    def run_finished(
        self,
        *,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        print_failures(
            test_results,
            session_teardown_failures=session_teardown_failures,
            session_teardown_output=session_teardown_output,
        )
        print_summary(
            test_results,
            session_teardown_failures=session_teardown_failures,
            total_duration=total_duration,
        )


class NullRunReporter:
    """Reporter adapter for callers that need structured results only."""

    def test_finished(self, test_result: TestResult) -> None:
        _ = test_result

    def run_finished(
        self,
        *,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        _ = (
            test_results,
            session_teardown_failures,
            session_teardown_output,
            total_duration,
        )


@dataclass(frozen=True)
class _DeferredRunFinished:
    test_results: list[TestResult]
    session_teardown_failures: list[TeardownFailure]
    session_teardown_output: str | None
    total_duration: float


class DeferredRunReporter:
    """Show test progress immediately but delay the final run summary."""

    def __init__(self, reporter: RunReporter) -> None:
        self._reporter: RunReporter = reporter
        self._run_finished: _DeferredRunFinished | None = None

    def test_finished(self, test_result: TestResult) -> None:
        self._reporter.test_finished(test_result)

    def run_finished(
        self,
        *,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        self._run_finished = _DeferredRunFinished(
            test_results=test_results,
            session_teardown_failures=session_teardown_failures,
            session_teardown_output=session_teardown_output,
            total_duration=total_duration,
        )

    def finish(self) -> None:
        """Emit the final summary after any post-run persistence succeeds."""
        run_finished = self._run_finished
        if run_finished is None:
            return
        self._reporter.run_finished(
            test_results=run_finished.test_results,
            session_teardown_failures=run_finished.session_teardown_failures,
            session_teardown_output=run_finished.session_teardown_output,
            total_duration=run_finished.total_duration,
        )


__all__ = [
    "ConsoleRunReporter",
    "DeferredRunReporter",
    "NullRunReporter",
    "RunReporter",
]
