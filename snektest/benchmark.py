"""Timing-budget assertions and result collection."""

import gc
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from math import ceil, isfinite
from statistics import fmean, median, pstdev
from types import TracebackType
from typing import Self, overload

from snektest.models import (
    AssertionFailure,
    BadRequestError,
    BenchmarkComparison,
    BenchmarkMeasurement,
)

_NANOSECONDS_PER_SECOND = 1_000_000_000


@dataclass
class BenchmarkCapture:
    """Completed measurements and baseline comparisons from one test body."""

    compare: Callable[[BenchmarkMeasurement], BenchmarkComparison] | None = None
    comparisons: list[BenchmarkComparison] = field(default_factory=list)
    measurements: list[BenchmarkMeasurement] = field(default_factory=list)


_benchmark_sink: ContextVar[BenchmarkCapture | None] = ContextVar(
    "snektest_benchmark_sink", default=None
)
_benchmark_context_lock = threading.Lock()


def _summarize(  # noqa: PLR0913
    durations_ns: list[int],
    *,
    name: str | None,
    warmup: int,
    median_budget_seconds: float | None,
    p95_budget_seconds: float | None,
    disable_gc: bool,
    median_regression_below: float | None,
    regression_noise_floor_seconds: float,
) -> BenchmarkMeasurement:
    """Reduce measured rounds using a nearest-rank p95."""
    ordered = sorted(durations_ns)
    p95_index = ceil(0.95 * len(ordered)) - 1
    return BenchmarkMeasurement(
        name=name,
        rounds=len(ordered),
        warmup=warmup,
        min_seconds=ordered[0] / _NANOSECONDS_PER_SECOND,
        median_seconds=median(ordered) / _NANOSECONDS_PER_SECOND,
        p95_seconds=ordered[p95_index] / _NANOSECONDS_PER_SECOND,
        mean_seconds=fmean(ordered) / _NANOSECONDS_PER_SECOND,
        stddev_seconds=pstdev(ordered) / _NANOSECONDS_PER_SECOND,
        median_budget_seconds=median_budget_seconds,
        p95_budget_seconds=p95_budget_seconds,
        disable_gc=disable_gc,
        median_regression_below=median_regression_below,
        regression_noise_floor_seconds=regression_noise_floor_seconds,
    )


def _record(measurement: BenchmarkMeasurement) -> None:
    sink = _benchmark_sink.get()
    if sink is not None:
        sink.measurements.append(measurement)


def _compare_with_baseline(measurement: BenchmarkMeasurement) -> None:
    """Apply the run-bound baseline policy after local absolute budgets pass."""
    sink = _benchmark_sink.get()
    if (
        sink is None
        or sink.compare is None
        or measurement.median_regression_below is None
    ):
        return
    comparison = sink.compare(measurement)
    comparison = replace(
        comparison,
        measurement_index=len(sink.measurements) - 1,
    )
    sink.comparisons.append(comparison)
    if comparison.verdict == "passed":
        return
    message = (
        f"benchmark[{comparison.name}] median {comparison.observed_median_seconds:.9g}s "
        f"regressed {comparison.change_ratio:+.1%} from baseline "
        f"{comparison.baseline_median_seconds:.9g}s; allowed increase is below "
        f"{comparison.regression_below:.1%} or "
        f"{comparison.noise_floor_seconds:.9g}s "
        f"(limit {comparison.limit_seconds:.9g}s)"
    )
    raise AssertionFailure(
        message,
        actual=comparison.observed_median_seconds,
        expected=comparison.limit_seconds,
        operator="<",
    )


@overload
def assert_benchmark(
    *,
    median_below: float,
    p95_below: float | None = None,
    name: str | None = None,
    rounds: int = 100,
    warmup: int = 10,
    disable_gc: bool = True,
    median_regression_below: float | None = None,
    regression_noise_floor: float = 0.0,
) -> BenchmarkContext: ...


@overload
def assert_benchmark(
    *,
    p95_below: float,
    median_below: float | None = None,
    name: str | None = None,
    rounds: int = 100,
    warmup: int = 10,
    disable_gc: bool = True,
    median_regression_below: float | None = None,
    regression_noise_floor: float = 0.0,
) -> BenchmarkContext: ...


def assert_benchmark(  # noqa: PLR0913
    *,
    median_below: float | None = None,
    p95_below: float | None = None,
    name: str | None = None,
    rounds: int = 100,
    warmup: int = 10,
    disable_gc: bool = True,
    median_regression_below: float | None = None,
    regression_noise_floor: float = 0.0,
) -> BenchmarkContext:
    """Assert the median and/or p95 duration of a repeated region.

    Budgets are seconds, and at least one of `median_below` or `p95_below` is
    required. Put one-time setup before the context, then loop the operation over
    `timing.rounds`; the iterator performs `warmup + rounds` iterations and
    excludes warmups from the statistics:

    ```python
    with assert_benchmark(median_below=0.01, rounds=100) as timing:
        for _ in timing.rounds:
            do_work()
    ```

    Sync and async work are both supported because the caller owns the loop body.
    GC is suspended during measured rounds by default and restored on exit.
    `median_regression_below` opts a named region into machine-bound baseline
    comparison when the runner receives `--benchmark-baseline`; the value is a
    fractional increase. `regression_noise_floor` supplies an absolute allowance
    in seconds. Ordinary runs continue to enforce only the absolute budgets.
    """
    return BenchmarkContext(
        median_below=median_below,
        p95_below=p95_below,
        name=name,
        rounds=rounds,
        warmup=warmup,
        disable_gc=disable_gc,
        median_regression_below=median_regression_below,
        regression_noise_floor=regression_noise_floor,
    )


class BenchmarkContext:
    """Context manager returned by `assert_benchmark`."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        median_below: float | None,
        rounds: int,
        warmup: int,
        disable_gc: bool,
        p95_below: float | None = None,
        name: str | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
        median_regression_below: float | None = None,
        regression_noise_floor: float = 0.0,
    ) -> None:
        self._median_below: float | None = median_below
        self._p95_below: float | None = p95_below
        self._name: str | None = name
        self._rounds: int = rounds
        self._warmup: int = warmup
        self._disable_gc: bool = disable_gc
        self._clock: Callable[[], int] = clock
        self._median_regression_below: float | None = median_regression_below
        self._regression_noise_floor: float = regression_noise_floor
        self._context_lock_acquired: bool = False
        self._durations_ns: list[int] = []
        self._rounds_iter: Generator[int] | None = None
        self._exhausted: bool = False
        self._gc_was_enabled: bool = False
        self._measurement: BenchmarkMeasurement | None = None
        self.has_exited: bool = False

    def __enter__(self) -> Self:  # noqa: C901
        if self._rounds < 1:
            msg = f"assert_benchmark rounds must be positive, got {self._rounds}."
            raise BadRequestError(msg)
        if self._warmup < 0:
            msg = f"assert_benchmark warmup cannot be negative, got {self._warmup}."
            raise BadRequestError(msg)
        if self._median_below is None and self._p95_below is None:
            msg = "assert_benchmark requires median_below and/or p95_below."
            raise BadRequestError(msg)
        if self._name is not None and not self._name.strip():
            msg = "assert_benchmark name cannot be empty or whitespace."
            raise BadRequestError(msg)
        if self._median_regression_below is not None and self._name is None:
            msg = "assert_benchmark requires name when median_regression_below is set."
            raise BadRequestError(msg)
        if self._median_regression_below is not None and (
            not isfinite(self._median_regression_below)
            or self._median_regression_below <= 0
        ):
            msg = (
                "assert_benchmark median_regression_below must be finite and positive, "
                f"got {self._median_regression_below}."
            )
            raise BadRequestError(msg)
        if (
            not isfinite(self._regression_noise_floor)
            or self._regression_noise_floor < 0
        ):
            msg = (
                "assert_benchmark regression_noise_floor must be finite and nonnegative, "
                f"got {self._regression_noise_floor}."
            )
            raise BadRequestError(msg)
        if self._median_regression_below is None and self._regression_noise_floor != 0:
            msg = (
                "assert_benchmark regression_noise_floor requires "
                "median_regression_below."
            )
            raise BadRequestError(msg)
        for name, budget in (
            ("median_below", self._median_below),
            ("p95_below", self._p95_below),
        ):
            if budget is not None and (not isfinite(budget) or budget <= 0):
                msg = (
                    f"assert_benchmark {name} must be finite and positive, "
                    f"got {budget}."
                )
                raise BadRequestError(msg)
        if not _benchmark_context_lock.acquire(blocking=False):
            msg = (
                "assert_benchmark contexts cannot overlap because concurrent "
                "regions distort timings and GC state."
            )
            raise BadRequestError(msg)
        self._context_lock_acquired = True
        self._gc_was_enabled = gc.isenabled()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.has_exited = True
        try:
            if exc_type is not None:
                return False
            self._finalize()
        finally:
            try:
                self._restore_gc()
            finally:
                if self._context_lock_acquired:
                    self._context_lock_acquired = False
                    _benchmark_context_lock.release()
        return False

    @property
    def rounds(self) -> Generator[int]:
        """Stateful iterator over warmup and measured rounds."""
        if self._rounds_iter is None:
            self._rounds_iter = self._run_rounds()
        return self._rounds_iter

    @property
    def min_seconds(self) -> float:
        return self._finished_measurement().min_seconds

    @property
    def median_seconds(self) -> float:
        return self._finished_measurement().median_seconds

    @property
    def p95_seconds(self) -> float:
        return self._finished_measurement().p95_seconds

    @property
    def mean_seconds(self) -> float:
        return self._finished_measurement().mean_seconds

    @property
    def stddev_seconds(self) -> float:
        return self._finished_measurement().stddev_seconds

    def _finished_measurement(self) -> BenchmarkMeasurement:
        if not self.has_exited:
            msg = "benchmark statistics were accessed before exiting the context"
            raise BadRequestError(msg)
        if self._measurement is None:
            msg = "benchmark statistics are unavailable because measurement did not finish"
            raise BadRequestError(msg)
        return self._measurement

    def _run_rounds(self) -> Generator[int]:
        """Time each yielded body and retain only post-warmup durations."""
        self._durations_ns = [0] * self._rounds
        for index in range(self._warmup + self._rounds):
            if index == self._warmup and self._disable_gc:
                gc.disable()
            started_ns = self._clock()
            yield index
            if index >= self._warmup:
                self._durations_ns[index - self._warmup] = self._clock() - started_ns
            else:
                _ = self._clock()
        self._exhausted = True

    def _restore_gc(self) -> None:
        if not self._disable_gc:
            return
        if self._gc_was_enabled:
            gc.enable()
        else:
            gc.disable()

    def _finalize(self) -> None:
        if self._rounds_iter is None:
            msg = (
                "assert_benchmark requires iterating `for _ in timing.rounds:`; "
                "the rounds iterator was never consumed."
            )
            raise BadRequestError(msg)
        if not self._exhausted:
            msg = (
                "assert_benchmark rounds iterator was only partially consumed; "
                "let the loop run to completion (do not break early)."
            )
            raise BadRequestError(msg)
        self._measurement = _summarize(
            self._durations_ns,
            name=self._name,
            warmup=self._warmup,
            median_budget_seconds=self._median_below,
            p95_budget_seconds=self._p95_below,
            disable_gc=self._disable_gc,
            median_regression_below=self._median_regression_below,
            regression_noise_floor_seconds=self._regression_noise_floor,
        )
        measurement = self._measurement
        _record(measurement)
        benchmark_label = (
            "benchmark"
            if measurement.name is None
            else f"benchmark[{measurement.name}]"
        )
        if (
            self._median_below is not None
            and not measurement.median_seconds < self._median_below
        ):
            message = (
                f"{benchmark_label} median {measurement.median_seconds:.9g}s is not below "
                f"budget {self._median_below:.9g}s"
            )
            raise AssertionFailure(
                message,
                actual=measurement.median_seconds,
                expected=self._median_below,
                operator="<",
            )
        if (
            self._p95_below is not None
            and not measurement.p95_seconds < self._p95_below
        ):
            message = (
                f"{benchmark_label} p95 {measurement.p95_seconds:.9g}s is not below "
                f"budget {self._p95_below:.9g}s"
            )
            raise AssertionFailure(
                message,
                actual=measurement.p95_seconds,
                expected=self._p95_below,
                operator="<",
            )
        _compare_with_baseline(measurement)


@contextmanager
def collect_benchmarks(
    *,
    compare: Callable[[BenchmarkMeasurement], BenchmarkComparison] | None = None,
) -> Generator[BenchmarkCapture]:
    """Bind a fresh benchmark sink for the duration of a test body."""
    capture = BenchmarkCapture(compare=compare)
    token: Token[BenchmarkCapture | None] = _benchmark_sink.set(capture)
    try:
        yield capture
    finally:
        _benchmark_sink.reset(token)
