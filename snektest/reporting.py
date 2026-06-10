"""Adapters for reporting test run progress and completion."""

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


__all__ = [
    "ConsoleRunReporter",
    "NullRunReporter",
    "RunReporter",
]
