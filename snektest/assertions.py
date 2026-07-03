from collections.abc import Generator
from contextvars import Token
from statistics import median
from types import TracebackType
from typing import Any, Self, cast, overload

from snektest.memory import (
    BackendName,
    MemoryBackend,
    create_backend,
    enter_measurement,
    exit_measurement,
    is_measurement_active,
    record_measurement,
)
from snektest.models import (
    AssertionFailure,
    BadRequestError,
    MemoryMeasurement,
    UnreachableError,
)


def assert_raises[T](
    *expected_exceptions: type[T],
    msg: str | None = None,
) -> RaisesContex[T]:
    return RaisesContex(*expected_exceptions, msg=msg)


class RaisesContex[T]:
    def __init__(self, *expected_exceptions: type[T], msg: str | None = None) -> None:
        self.msg = msg
        self.expected_exceptions = expected_exceptions
        self._exception: T | None = None
        self.has_exited = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.has_exited = True
        if exc_type is None:
            message = (
                self.msg or "Expected to raise an exception but no exception was raised"
            )
            raise AssertionFailure(
                message,
                actual=None,
                expected=self.expected_exceptions,
            )
        if not isinstance(exc_value, self.expected_exceptions):
            expected_exceptions_display_name = " | ".join(
                e.__name__ for e in self.expected_exceptions
            )
            message = (
                self.msg
                or f"Expected to raise {expected_exceptions_display_name} but raised {type(exc_value).__name__}"
            )
            raise AssertionFailure(
                message,
                actual=exc_value,
                expected=self.expected_exceptions,
            )
        self._exception = exc_value
        return True

    @property
    def exception(self) -> T:
        if not self.has_exited:
            msg = "Exception under assert_raises was accessed before exiting the context manager"
            raise BadRequestError(msg)
        return cast("T", self._exception)


_MIN_SLOPE_ROUNDS = 10
"""Fewest rounds a `slope_below` budget can be measured over meaningfully."""

_MIN_SLOPE_POINTS = 2
"""Fewest retained-bytes samples a Theil-Sen slope can be fit through."""


def _theil_sen_slope(values: list[int]) -> float:
    """Median of pairwise slopes of `values` against their 0-based index.

    Bytes-per-round growth resistant to a single-round GC/arena spike; the
    median of every `(y_j - y_i) / (j - i)` needs at least two points.
    """
    pairwise_slopes = [
        (values[later] - values[earlier]) / (later - earlier)
        for earlier in range(len(values))
        for later in range(earlier + 1, len(values))
    ]
    return median(pairwise_slopes)


@overload
def assert_memory(
    *,
    peak_below: int,
    slope_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: BackendName = "tracemalloc",
) -> MemoryContext: ...
@overload
def assert_memory(
    *,
    slope_below: int,
    peak_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: BackendName = "tracemalloc",
) -> MemoryContext: ...
def assert_memory(
    *,
    peak_below: int | None = None,
    slope_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: BackendName = "tracemalloc",
) -> MemoryContext:
    """Assert a code region's peak allocation and/or leak-free growth.

    Budgets are bytes-as-`int`. At least one of `peak_below` / `slope_below` is
    required (a budgetless call is a type error). For whole-block peak checks,
    wrap the region directly; for leak detection, loop the work over `m.rounds`
    (which requires `rounds >= 10` when `slope_below` is set):

    ```python
    with assert_memory(peak_below=8 * 1024 * 1024) as m:
        payload = bytearray(1024 * 1024)
        del payload

    with assert_memory(slope_below=1024, rounds=20) as leak:
        for _ in leak.rounds:
            do_work()
    ```

    Budgets are enforced on `__exit__` (raising `AssertionFailure`); `m.peak_bytes`
    and `m.growth_slope` stay readable afterwards for custom assertions. Cannot
    be nested. `backend` is generic so swapping tracers never edits call sites.
    """
    return MemoryContext(
        peak_below=peak_below,
        slope_below=slope_below,
        rounds=rounds,
        warmup=warmup,
        backend=backend,
    )


class MemoryContext:
    """Context manager returned by `assert_memory`; see that function for usage.

    Whole-block mode (default `rounds=1`, `m.rounds` untouched) takes a single
    peak/retained sample over the block. Rounds mode iterates `warmup + rounds`
    times through `m.rounds`, resetting the peak watermark each round and
    dropping warmup samples from the Theil-Sen slope fit.
    """

    def __init__(
        self,
        *,
        peak_below: int | None,
        slope_below: int | None,
        rounds: int,
        warmup: int,
        backend: BackendName,
    ) -> None:
        self._peak_below: int | None = peak_below
        self._slope_below: int | None = slope_below
        self._rounds: int = rounds
        self._warmup: int = warmup
        self._backend: MemoryBackend = create_backend(backend)
        self._retained_samples: list[int] = []
        self._peak_max: int = 0
        self._rounds_iter: Generator[int] | None = None
        self._exhausted: bool = False
        self._guard_token: Token[bool] | None = None
        self.has_exited: bool = False
        self._peak_bytes: int | None = None
        self._growth_slope: float | None = None

    def __enter__(self) -> Self:
        if is_measurement_active():
            msg = (
                "assert_memory cannot be nested: a second measurement inside an "
                "active one would corrupt the outer peak watermark."
            )
            raise BadRequestError(msg)
        if self._peak_below is None and self._slope_below is None:
            msg = "assert_memory requires at least one of peak_below or slope_below."
            raise BadRequestError(msg)
        if self._slope_below is not None and self._rounds < _MIN_SLOPE_ROUNDS:
            msg = (
                f"assert_memory(slope_below=...) needs rounds >= {_MIN_SLOPE_ROUNDS} "
                f"for a meaningful slope, got rounds={self._rounds}."
            )
            raise BadRequestError(msg)
        self._guard_token = enter_measurement()
        self._backend.start()
        self._backend.reset_peak()
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
            self._backend.stop()
            if self._guard_token is not None:
                exit_measurement(self._guard_token)
        return False

    @property
    def rounds(self) -> Generator[int]:
        """Stateful iterator over warmup + measured rounds; loop your work over it."""
        if self._rounds_iter is None:
            self._rounds_iter = self._run_rounds()
        return self._rounds_iter

    @property
    def peak_bytes(self) -> int:
        if not self.has_exited:
            msg = "m.peak_bytes was accessed before exiting the assert_memory context"
            raise BadRequestError(msg)
        return cast("int", self._peak_bytes)

    @property
    def growth_slope(self) -> float:
        if not self.has_exited:
            msg = "m.growth_slope was accessed before exiting the assert_memory context"
            raise BadRequestError(msg)
        if self._growth_slope is None:
            msg = (
                "m.growth_slope is only available after iterating m.rounds; "
                "a whole-block measurement has no slope."
            )
            raise BadRequestError(msg)
        return self._growth_slope

    def _run_rounds(self) -> Generator[int]:
        """Yield each round index, sampling the round on resume after the body.

        The retained-samples list is preallocated to its final size before any
        measured round so per-round list growth does not masquerade as leaked
        memory in the slope fit.
        """
        self._retained_samples = [0] * self._rounds
        for index in range(self._warmup + self._rounds):
            self._backend.reset_peak()
            yield index
            self._record_round(index)
        self._exhausted = True

    def _record_round(self, index: int) -> None:
        """Sample a finished round, keeping only post-warmup rounds for the fit.

        Only plain ints are retained (into a preallocated slot and a running
        peak max), so the machinery's own per-round allocation stays negligible
        and does not contaminate the growth slope.
        """
        sample = self._backend.sample()
        if index >= self._warmup:
            self._retained_samples[index - self._warmup] = sample.retained_bytes
            self._peak_max = max(self._peak_max, sample.peak_bytes)

    def _finalize(self) -> None:
        """Reduce samples to peak/slope, guarding misuse of the rounds iterator."""
        if self._rounds_iter is None:
            if self._rounds > 1:
                msg = (
                    f"assert_memory(rounds={self._rounds}) requires iterating "
                    "`for _ in m.rounds:`; the rounds iterator was never consumed."
                )
                raise BadRequestError(msg)
            self._peak_bytes = self._backend.sample().peak_bytes
            self._growth_slope = None
            self._assert_budgets(measured_rounds=1)
            return
        if not self._exhausted:
            msg = (
                "assert_memory rounds iterator was only partially consumed; let "
                "`for _ in m.rounds:` run to completion (do not break early)."
            )
            raise BadRequestError(msg)
        self._peak_bytes = self._peak_max
        retained_bytes = self._retained_samples
        self._growth_slope = (
            _theil_sen_slope(retained_bytes)
            if len(retained_bytes) >= _MIN_SLOPE_POINTS
            else None
        )
        self._assert_budgets(measured_rounds=len(self._retained_samples))

    def _assert_budgets(self, *, measured_rounds: int) -> None:
        """Compare measured numbers to budgets, recording the result on pass."""
        if self._peak_bytes is None:
            msg = "assert_memory produced no peak sample. This shouldn't be possible!"
            raise UnreachableError(msg)
        if self._peak_below is not None and not self._peak_bytes < self._peak_below:
            message = f"peak memory {self._peak_bytes} bytes exceeds budget {self._peak_below} bytes"
            raise AssertionFailure(
                message,
                actual=self._peak_bytes,
                expected=self._peak_below,
                operator="<",
            )
        if self._slope_below is not None:
            if self._growth_slope is None:
                msg = "slope budget without a slope. This shouldn't be possible!"
                raise UnreachableError(msg)
            if not self._growth_slope < self._slope_below:
                message = f"memory growth slope {self._growth_slope:.1f} bytes/round exceeds budget {self._slope_below} bytes/round"
                raise AssertionFailure(
                    message,
                    actual=self._growth_slope,
                    expected=self._slope_below,
                    operator="<",
                )
        record_measurement(
            MemoryMeasurement(
                peak_bytes=self._peak_bytes,
                growth_slope=self._growth_slope,
                rounds=measured_rounds,
                peak_budget=self._peak_below,
                slope_budget=self._slope_below,
            )
        )


def assert_eq(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual == expected."""
    if actual != expected:
        message = msg or f"{actual!r} != {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="==",
        )


def assert_ne(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual != expected."""
    if actual == expected:
        message = msg or f"{actual!r} == {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="!=",
        )


def assert_true(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is True (identity check, not truthiness)."""
    if value is not True:
        message = msg or f"{value!r} is not True"
        raise AssertionFailure(
            message,
            actual=value,
            expected=True,
            operator="is",
        )


def assert_false(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is False (identity check, not falsiness)."""
    if value is not False:
        message = msg or f"{value!r} is not False"
        raise AssertionFailure(
            message,
            actual=value,
            expected=False,
            operator="is",
        )


def assert_is_none(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is None."""
    if value is not None:
        message = msg or f"{value!r} is not None"
        raise AssertionFailure(
            message,
            actual=value,
            expected=None,
            operator="is",
        )


def assert_is_not_none[T](value: T | None, *, msg: str | None = None) -> T:
    """Assert that value is not None, returning the narrowed value."""
    if value is None:
        message = msg or "value is None"
        raise AssertionFailure(
            message,
            actual=value,
            expected="not None",
            operator="is not",
        )
    return value


def assert_is(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual is expected (identity check)."""
    if actual is not expected:
        message = msg or f"{actual!r} is not {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="is",
        )


def assert_is_not(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual is not expected (identity check)."""
    if actual is expected:
        message = msg or f"{actual!r} is {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="is not",
        )


def assert_lt(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual < expected."""
    if not actual < expected:
        message = msg or f"{actual!r} >= {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="<",
        )


def assert_gt(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual > expected."""
    if not actual > expected:
        message = msg or f"{actual!r} <= {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator=">",
        )


def assert_le(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual <= expected."""
    if not actual <= expected:
        message = msg or f"{actual!r} > {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="<=",
        )


def assert_ge(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual >= expected."""
    if not actual >= expected:
        message = msg or f"{actual!r} < {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator=">=",
        )


def assert_in(member: Any, container: Any, *, msg: str | None = None) -> None:
    """Assert that member in container."""
    if member not in container:
        message = msg or f"{member!r} not found in {container!r}"
        raise AssertionFailure(
            message,
            actual=member,
            expected=container,
            operator="in",
        )


def assert_not_in(member: Any, container: Any, *, msg: str | None = None) -> None:
    """Assert that member not in container."""
    if member in container:
        message = msg or f"{member!r} found in {container!r}"
        raise AssertionFailure(
            message,
            actual=member,
            expected=container,
            operator="not in",
        )


@overload
def assert_isinstance[T](
    obj: object, classinfo: type[T], *, msg: str | None = None
) -> T: ...
@overload
def assert_isinstance(
    obj: object, classinfo: tuple[type, ...], *, msg: str | None = None
) -> object: ...
def assert_isinstance(
    obj: Any, classinfo: type | tuple[type, ...], *, msg: str | None = None
) -> Any:
    """Assert that isinstance(obj, classinfo) is True, returning the narrowed value."""
    if not isinstance(obj, classinfo):
        type_name = (
            classinfo.__name__ if isinstance(classinfo, type) else str(classinfo)
        )
        message = msg or f"{obj!r} is not an instance of {type_name}"
        raise AssertionFailure(
            message,
            actual=type(obj).__name__,
            expected=type_name,
            operator="isinstance",
        )
    return obj


def assert_not_isinstance(
    obj: Any, classinfo: type | tuple[type, ...], *, msg: str | None = None
) -> None:
    """Assert that isinstance(obj, classinfo) is False."""
    if isinstance(obj, classinfo):
        type_name = (
            classinfo.__name__ if isinstance(classinfo, type) else str(classinfo)
        )
        message = msg or f"{obj!r} is an instance of {type_name}"
        raise AssertionFailure(
            message,
            actual=type(obj).__name__,
            expected=f"not {type_name}",
            operator="not isinstance",
        )


def assert_len(obj: Any, expected_length: int, *, msg: str | None = None) -> None:
    """Assert that len(obj) == expected_length."""
    actual_length = len(obj)
    if actual_length != expected_length:
        message = msg or f"Length {actual_length} != {expected_length}"
        raise AssertionFailure(
            message,
            actual=actual_length,
            expected=expected_length,
            operator="len ==",
        )


def fail(msg: str | None = None) -> None:
    """Unconditionally raise an AssertionFailure.

    Args:
        msg: Optional custom message
    """
    message = msg or "Assertion failed"
    raise AssertionFailure(message)
