"""Domain models for collected tests, results, filters, and framework errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Literal, override

from snektest.annotations import Coroutine

DEFAULT_CLEANUP_TIMEOUT_SECONDS = 60.0
"""Maximum async cleanup time when no shorter run timeout is configured."""


class SnektestError(Exception):
    """Base class for catchable snektest framework errors."""


class CollectionError(SnektestError):
    """Raised when requested tests cannot form a valid collection plan."""


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
    def to_dict(
        params: tuple[list[Param[Any]], ...],
    ) -> dict[str, tuple[Param[Any], ...]]:
        """Create a dictionary that contains all possible params combinations.

        For tests with no parameters, returns {"": ()} to ensure the test runs once.
        """
        if not params:
            return {"": ()}

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


@dataclass(frozen=True)
class TestResult:
    captured_output: str
    duration: float
    fixture_teardown_failures: tuple[TeardownFailure, ...]
    fixture_teardown_output: str | None
    markers: tuple[str, ...]
    name: TestName
    result: PassedResult | FailedResult | ErrorResult
    warnings: tuple[str, ...]
    ordinal: int = 0
    background_failures: tuple[BackgroundFailure, ...] = ()
