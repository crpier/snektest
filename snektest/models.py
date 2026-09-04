"""Domain models for collected tests, results, filters, and framework errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import product
from math import prod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, override

if TYPE_CHECKING:
    from snektest.benchmark_baseline import MachineFingerprint

from snektest.annotations import Coroutine

DEFAULT_CLEANUP_TIMEOUT_SECONDS = 60.0
"""Maximum async cleanup time when no shorter run timeout is configured."""

_MAX_PARAMETER_CASES = 10_000
"""Largest Cartesian product accepted for one parameterized test."""


class SnektestError(Exception):
    """Base class for catchable snektest framework errors."""


class CollectionError(SnektestError):
    """Raised when requested tests cannot form a valid collection plan."""

    def __init__(self, message: str) -> None:
        self.collection_diagnostic: ExceptionDiagnostic | None = None
        self.collection_output = ""
        self.collection_warnings: tuple[str, ...] = ()
        super().__init__(message)


class EmptyCollectionError(CollectionError):
    """Raised when one or more filters produce no tests without explicit opt-in."""


class InvalidTestDefinitionError(CollectionError):
    """Raised when test metadata cannot produce a complete collection plan."""


class ArgsError(SnektestError): ...


class UnreachableError(BaseException):
    """Internal invariant failure that must bypass ordinary error classification."""


class RunInfrastructureError(SnektestError):
    """A child-process failure that prevents the selected run from completing."""


class BadRequestError(SnektestError):
    """Raised when test configuration or programmatic input is invalid."""


class _OutcomeSignal(BaseException):
    """Carry an intentional result through code that catches ordinary exceptions."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _ExpectedFailureSignal(_OutcomeSignal):
    """Stop a test at an explicitly acknowledged known defect."""


class _SkipSignal(_OutcomeSignal):
    """Stop a test because its runtime environment is unavailable."""


class TestTimeoutError(SnektestError):
    """Raised when an async test exceeds its configured timeout.

    A normal `Exception` so it flows through the same error-reporting path as any
    other unexpected exception, surfacing as an ERROR result with a clear message.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"exceeded the configured timeout of {timeout:g}s")


class FixtureError(SnektestError):
    """When a fixture is defined or used incorrectly."""


class FixtureTaskLeakError(SnektestError):
    """Raised when fixture teardown leaves owned tasks pending."""

    def __init__(self, fixture_name: str, task_count: int) -> None:
        self.fixture_name = fixture_name
        self.task_count = task_count
        task_word = "task" if task_count == 1 else "tasks"
        super().__init__(
            f"fixture {fixture_name} leaked {task_count} pending {task_word}"
        )


class FixtureTeardownTimeoutError(SnektestError):
    """Raised when an async fixture exceeds its cleanup ceiling."""

    def __init__(self, fixture_name: str, timeout: float) -> None:
        self.fixture_name = fixture_name
        self.timeout = timeout
        super().__init__(
            f"fixture {fixture_name} teardown exceeded the cleanup timeout of {timeout:g}s"
        )


class AssertionFailure(AssertionError, SnektestError):  # noqa: N818
    """Raised when a snektest assertion helper rejects an observed value."""

    def __init__(
        self,
        message: str,
        *,
        actual: Any = None,
        expected: Any = None,
        operator: str | None = None,
    ) -> None:
        super().__init__(message)
        self.actual = actual
        self.expected = expected
        self.operator = operator


class FilterItem:
    """Represents a single test filter item"""

    def __init__(self, raw_input: str) -> None:
        if "::" not in raw_input:
            path = Path(raw_input)
            function_name = None
            params = None
        else:
            file_part, rest = raw_input.split("::", 1)
            if rest == "":
                msg = f"Invalid test filter - nothing given after semicolon in '{raw_input}'"
                raise ArgsError(msg)

            path = Path(file_part)

            if "[" in rest:
                if not rest.endswith("]"):
                    msg = f"Invalid test filter - unterminated `[` in '{raw_input}'"
                    raise ArgsError(msg)
                rest = rest.removesuffix("]")
                function_name, params = rest.split("[", 1)
            else:
                function_name = rest
                params = None

        if not path.exists():
            msg = f"Invalid test filter - provided path does not exist in '{raw_input}'"
            raise ArgsError(msg)

        if path.is_file() and path.suffix != ".py":
            msg = f"Invalid test filter - file is not a Python script in '{raw_input}'"
            raise ArgsError(msg)

        if path.is_file() and not path.name.startswith("test_"):
            msg = (
                f"Invalid test filter - file does not start with _test in '{raw_input}'"
            )
            raise ArgsError(msg)

        if function_name is not None and not function_name.isidentifier():
            msg = f"Invalid test filter - invalid identifier {function_name} in '{raw_input}'"
            raise ArgsError(msg)

        self.file_path = path
        self.function_name = function_name
        self.params = params

    @override
    def __str__(self) -> str:
        result = str(self.file_path)
        if self.function_name is not None:
            result += f"::{self.function_name}"
        if self.params:
            result += f"[{self.params}]"
        return result

    @override
    def __repr__(self) -> str:
        return f"FilterItem(file_path={self.file_path!r}, function_name={self.function_name!r}, params={self.params!r})"


# Set kw_only so we can write attributes in the order they appear
@dataclass(frozen=True, kw_only=True)
class TestName:
    file_path: Path
    func_name: str
    params_part: str
    resolved_file_path: Path | None = None

    @override
    def __str__(self) -> str:
        result = str(self.file_path)
        result += f"::{self.func_name}"
        if self.params_part:
            result += f"[{self.params_part}]"
        return result


@dataclass(frozen=True)
class BenchmarkMeasurement:
    """Timing statistics for the measured rounds of one benchmark test.

    Durations, optional budgets, and the regression noise floor are seconds.
    `median_regression_below` is a fractional increase. `p95_seconds` uses the
    nearest-rank percentile, and `stddev_seconds` is the population standard
    deviation across measured rounds.
    """

    name: str | None
    rounds: int
    warmup: int
    min_seconds: float
    median_seconds: float
    p95_seconds: float
    mean_seconds: float
    stddev_seconds: float
    median_budget_seconds: float | None
    p95_budget_seconds: float | None
    disable_gc: bool = True
    median_regression_below: float | None = None
    regression_noise_floor_seconds: float = 0.0


@dataclass(frozen=True)
class BenchmarkComparison:
    """One current median compared with its machine-bound stored baseline."""

    allowed_increase_seconds: float
    baseline_median_seconds: float
    change_ratio: float
    limit_seconds: float
    measurement_index: int
    name: str
    noise_floor_seconds: float
    observed_median_seconds: float
    regression_below: float
    verdict: Literal["passed", "regressed"]


@dataclass(frozen=True)
class MemoryMeasurement:
    """One `assert_memory` result: measured numbers plus the budgets that gated them.

    `growth_slope` is bytes-per-round (Theil-Sen), `None` for a whole-block
    single sample. `rounds` is the count of measured (post-warmup) rounds.
    Budgets are `None` when the corresponding check was not requested; the
    presenter renders only the budgets that were set.
    """

    peak_bytes: int
    growth_slope: float | None
    rounds: int
    peak_budget: int | None
    slope_budget: int | None


@dataclass(frozen=True)
class PassedResult:
    measurements: tuple[MemoryMeasurement, ...] = ()
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()


type TestFunction = Callable[..., Coroutine[None] | None]


@dataclass(frozen=True)
class TestCase:
    """Collected test case with decorator metadata resolved once.

    Collection owns discovery and parameter expansion. Execution only needs this
    interface: call the prepared test and attach its already-resolved metadata to
    the result.
    """

    function: TestFunction
    markers: tuple[str, ...]
    name: TestName
    expected_failure_reason: str | None = None
    mutex: str | None = None
    ordinal: int = 0
    param_values: tuple[object, ...] = ()

    def call(self) -> Coroutine[None] | None:
        """Run the underlying test function with its collected parameter values."""
        return self.function(*self.param_values)


@dataclass
class Param[T]:
    value: T
    name: str

    @staticmethod
    def to_dict(  # noqa: C901
        params: tuple[list[Param[Any]], ...],
    ) -> dict[str, tuple[Param[Any], ...]]:
        """Create a dictionary that contains all possible params combinations.

        For tests with no parameters, returns {"": ()} to ensure the test runs once.
        """
        if not params:
            return {"": ()}

        case_count = prod(len(axis) for axis in params)
        if case_count > _MAX_PARAMETER_CASES:
            msg = (
                f"Parameterized test expands to {case_count} cases; "
                f"maximum is {_MAX_PARAMETER_CASES}"
            )
            raise BadRequestError(msg)

        for axis_index, axis in enumerate(params, start=1):
            if not axis:
                msg = (
                    f"Parameterized test parameter list {axis_index} must not be empty"
                )
                raise BadRequestError(msg)
            axis_names: set[str] = set()
            for param in axis:
                if not param.name:
                    msg = "Parameterized case names must not be empty"
                    raise BadRequestError(msg)
                if any(token in param.name for token in (", ", "[", "]")):
                    msg = (
                        f"Parameterized case name `{param.name}` is ambiguous; "
                        "names must not contain `, `, `[` or `]`"
                    )
                    raise BadRequestError(msg)
                if param.name in axis_names:
                    msg = f"Parameterized case name `{param.name}` is not unique"
                    raise BadRequestError(msg)
                axis_names.add(param.name)

        combinations = product(*params)
        result: dict[str, tuple[Param[Any], ...]] = {}
        for combination in combinations:
            case_name = ", ".join([param.name for param in combination])
            if case_name in result:
                msg = f"Parameterized case name `{case_name}` is not unique"
                raise BadRequestError(msg)
            result[case_name] = combination
        return result


@dataclass(frozen=True)
class DiagnosticFrame:
    """One user-code frame captured before a live traceback is released."""

    filename: str
    function_name: str
    lineno: int
    source_line: str | None


@dataclass(frozen=True)
class DiagnosticRepr:
    """Bounded representation of a value that cannot cross a process directly."""

    text: str

    @override
    def __repr__(self) -> str:
        return self.text


@dataclass(frozen=True)
class DiagnosticList:
    """Immutable snapshot of a list used for assertion diff rendering."""

    items: tuple[DiagnosticValue, ...]


@dataclass(frozen=True)
class DiagnosticDict:
    """Immutable snapshot of a dictionary used for assertion diff rendering."""

    entries: tuple[tuple[DiagnosticValue, DiagnosticValue], ...]


type DiagnosticValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | DiagnosticRepr
    | DiagnosticList
    | DiagnosticDict
)


@dataclass(frozen=True)
class AssertionDiagnostic:
    """Process-neutral data needed to reproduce assertion presentation."""

    actual: DiagnosticValue | None
    expected: DiagnosticValue | None
    kind: Literal["plain", "list", "dict", "multiline_string"]
    message: str


@dataclass(frozen=True)
class ExceptionDiagnostic:
    """Immutable exception details safe to retain or send to another process."""

    frames: tuple[DiagnosticFrame, ...]
    message: str
    qualified_type_name: str
    type_name: str
    assertion: AssertionDiagnostic | None = None
    cause: ExceptionDiagnostic | None = None
    context: ExceptionDiagnostic | None = None
    exceptions: tuple[ExceptionDiagnostic, ...] = ()
    notes: tuple[str, ...] = ()
    suppress_context: bool = False


@dataclass(frozen=True)
class SkippedResult:
    """A test that stopped because its runtime environment was unavailable."""

    reason: str
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()


@dataclass(frozen=True)
class ExpectedFailureResult:
    """A test stopped at an explicitly acknowledged known defect."""

    reason: str
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()
    exception: ExceptionDiagnostic | None = None


@dataclass(frozen=True)
class UnexpectedPassResult:
    """A statically expected failure whose test body passed."""

    reason: str
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()


@dataclass(frozen=True)
class FailedResult:
    exception: ExceptionDiagnostic
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()


@dataclass(frozen=True)
class ErrorResult:
    exception: ExceptionDiagnostic
    benchmarks: tuple[BenchmarkMeasurement, ...] = ()
    benchmark_comparisons: tuple[BenchmarkComparison, ...] = ()


@dataclass
class CollectionDiagnostics:
    """Output and warnings captured while building the canonical test plan."""

    output: str = ""
    warnings: tuple[str, ...] = ()


@dataclass
class RunTeardownDiagnostics:
    """Output and warnings collected after test-case execution finishes."""

    run_output: str | None = None
    run_warnings: tuple[str, ...] = ()
    session_output: str | None = None
    session_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeardownFailure:
    """Represent one fixture teardown failure."""

    exception: ExceptionDiagnostic
    fixture_name: str


@dataclass(frozen=True)
class BackgroundFailure:
    """Process-neutral failure observed outside the test body's call stack."""

    exception: ExceptionDiagnostic
    label: str
    origin: Literal["thread", "thread_leak", "unraisable"]


type TestStatus = Literal[
    "passed",
    "skipped",
    "expected_failure",
    "unexpected_pass",
    "failed",
    "error",
]


@dataclass(frozen=True)
class TestResult:
    captured_output: str
    duration: float
    fixture_teardown_failures: tuple[TeardownFailure, ...]
    fixture_teardown_output: str | None
    markers: tuple[str, ...]
    name: TestName
    result: (
        PassedResult
        | SkippedResult
        | ExpectedFailureResult
        | UnexpectedPassResult
        | FailedResult
        | ErrorResult
    )
    warnings: tuple[str, ...]
    ordinal: int = 0
    background_failures: tuple[BackgroundFailure, ...] = ()

    @property
    def status(self) -> TestStatus:
        """Return the canonical status shared by every reporting adapter."""
        match self.result:
            case PassedResult():
                return "passed"
            case SkippedResult():
                return "skipped"
            case ExpectedFailureResult():
                return "expected_failure"
            case UnexpectedPassResult():
                return "unexpected_pass"
            case FailedResult():
                return "failed"
            case ErrorResult():
                return "error"


@dataclass(frozen=True)
class BenchmarkBaselineRun:
    """Machine-readable metadata for one compare or update CLI mode."""

    mode: Literal["compare", "update"]
    path: str
    machine: MachineFingerprint | None = None
    updated_entries: int = 0
    written: bool = False


@dataclass
class RunResult:
    """One normalized completed run consumed by every reporting adapter."""

    total_tests: int
    passed: int
    skipped: int
    expected_failures: int
    unexpected_passes: int
    failed: int
    errors: int
    fixture_teardown_failed: int
    session_teardown_failed: int
    test_results: list[TestResult]
    session_teardown_failures: list[TeardownFailure]
    collection_output: str = ""
    collection_warnings: tuple[str, ...] = ()
    run_teardown_failed: int = 0
    run_teardown_failures: list[TeardownFailure] = field(default_factory=list)
    run_teardown_output: str | None = None
    run_teardown_warnings: tuple[str, ...] = ()
    session_teardown_output: str | None = None
    session_teardown_warnings: tuple[str, ...] = ()
    benchmark_baseline: BenchmarkBaselineRun | None = None
    total_duration: float = 0.0

    @classmethod
    def from_execution(  # noqa: PLR0913
        cls,
        *,
        run_teardown_failures: list[TeardownFailure],
        run_teardown_output: str | None,
        run_teardown_warnings: tuple[str, ...],
        session_teardown_failures: list[TeardownFailure],
        session_teardown_output: str | None,
        session_teardown_warnings: tuple[str, ...],
        test_results: list[TestResult],
        total_duration: float,
        collection_output: str = "",
        collection_warnings: tuple[str, ...] = (),
    ) -> RunResult:
        """Normalize execution details and calculate status counts once."""
        return cls(
            total_tests=len(test_results),
            passed=sum(1 for result in test_results if result.status == "passed"),
            skipped=sum(1 for result in test_results if result.status == "skipped"),
            expected_failures=sum(
                1 for result in test_results if result.status == "expected_failure"
            ),
            unexpected_passes=sum(
                1 for result in test_results if result.status == "unexpected_pass"
            ),
            failed=sum(1 for result in test_results if result.status == "failed"),
            errors=sum(1 for result in test_results if result.status == "error"),
            fixture_teardown_failed=sum(
                len(result.fixture_teardown_failures) for result in test_results
            ),
            collection_output=collection_output,
            collection_warnings=collection_warnings,
            run_teardown_failed=len(run_teardown_failures),
            session_teardown_failed=len(session_teardown_failures),
            run_teardown_failures=run_teardown_failures,
            run_teardown_output=run_teardown_output,
            run_teardown_warnings=run_teardown_warnings,
            session_teardown_failures=session_teardown_failures,
            session_teardown_output=session_teardown_output,
            session_teardown_warnings=session_teardown_warnings,
            test_results=test_results,
            total_duration=total_duration,
        )

    @property
    def warnings(self) -> tuple[str, ...]:
        """Return warnings from collection, tests, and fixture teardown in order."""
        return (
            *self.collection_warnings,
            *(warning for result in self.test_results for warning in result.warnings),
            *self.session_teardown_warnings,
            *self.run_teardown_warnings,
        )

    @property
    def exit_code(self) -> int:
        """Return the command status implied by outcomes and teardown failures."""
        has_failures = (
            self.failed > 0
            or self.errors > 0
            or self.unexpected_passes > 0
            or self.fixture_teardown_failed > 0
            or self.run_teardown_failed > 0
            or self.session_teardown_failed > 0
        )
        return 1 if has_failures else 0
