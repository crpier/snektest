"""Adapters for reporting test run progress and completion."""

from dataclasses import dataclass, replace
from typing import Protocol

from snektest.models import PassedResult, TeardownFailure, TestResult
from snektest.presenter import print_failures, print_summary, print_test_result


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

    def run_finished(  # noqa: PLR0913
        self,
        *,
        run_teardown_failures: list[TeardownFailure],
        run_teardown_output: str | None,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        """Observe final run results after session fixture teardown."""


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

    retain_passed_output = False

    def test_finished(self, test_result: TestResult) -> None:
        print_test_result(test_result)

    def run_finished(  # noqa: PLR0913
        self,
        *,
        run_teardown_failures: list[TeardownFailure],
        run_teardown_output: str | None,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        print_failures(
            test_results,
            run_teardown_failures=run_teardown_failures,
            run_teardown_output=run_teardown_output,
            session_teardown_failures=session_teardown_failures,
            session_teardown_output=session_teardown_output,
        )
        print_summary(
            test_results,
            run_teardown_failures=run_teardown_failures,
            session_teardown_failures=session_teardown_failures,
            total_duration=total_duration,
        )


class NullRunReporter:
    """Reporter adapter for callers that need structured results only."""

    def __init__(self, *, retain_passed_output: bool = True) -> None:
        self.retain_passed_output: bool = retain_passed_output

    def test_finished(self, test_result: TestResult) -> None:
        _ = test_result

    def run_finished(  # noqa: PLR0913
        self,
        *,
        run_teardown_failures: list[TeardownFailure],
        run_teardown_output: str | None,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        _ = (
            run_teardown_failures,
            run_teardown_output,
            test_results,
            session_teardown_failures,
            session_teardown_output,
            total_duration,
        )


@dataclass(frozen=True)
class _DeferredRunFinished:
    run_teardown_failures: list[TeardownFailure]
    run_teardown_output: str | None
    test_results: list[TestResult]
    session_teardown_failures: list[TeardownFailure]
    session_teardown_output: str | None
    total_duration: float


class DeferredRunReporter:
    """Show test progress immediately but delay the final run summary."""

    def __init__(self, reporter: RunReporter) -> None:
        self._reporter: RunReporter = reporter
        self._run_finished: _DeferredRunFinished | None = None
        self.retain_passed_output: bool = reporter.retain_passed_output

    def test_finished(self, test_result: TestResult) -> None:
        self._reporter.test_finished(test_result)

    def run_finished(  # noqa: PLR0913
        self,
        *,
        run_teardown_failures: list[TeardownFailure],
        run_teardown_output: str | None,
        test_results: list[TestResult],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        total_duration: float,
    ) -> None:
        self._run_finished = _DeferredRunFinished(
            run_teardown_failures=run_teardown_failures,
            run_teardown_output=run_teardown_output,
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
            run_teardown_failures=run_finished.run_teardown_failures,
            run_teardown_output=run_finished.run_teardown_output,
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
