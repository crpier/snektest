from __future__ import annotations

from collections.abc import Iterator
from statistics import median
from types import TracebackType
from typing import Any, Literal, Self, cast, overload

from snektest.memory import (
    MemoryBackend,
    TracemallocBackend,
    append_memory_measurement,
    mark_memory_active,
    memory_is_active,
)
from snektest.models import AssertionFailure, BadRequestError, MemoryMeasurement

MIN_SLOPE_ROUNDS = 10
MIN_SLOPE_SAMPLE_COUNT = 2


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


class MemoryRounds:
    def __init__(self, context: MemoryContext) -> None:
        self._context: MemoryContext = context
        self._yielded_count: int = 0

    def __iter__(self) -> Iterator[int]:
        return self

    def __next__(self) -> int:
        self._context.mark_rounds_touched()
        if self._yielded_count > 0:
            self._context.record_completed_round(self._yielded_count - 1)
            self._context.reset_round_peak()
        if self._yielded_count >= self._context.total_rounds:
            self._context.mark_rounds_exhausted()
            raise StopIteration
        round_index = self._yielded_count
        self._yielded_count += 1
        return round_index


class MemoryContext:
    def __init__(
        self,
        *,
        peak_below: int | None,
        slope_below: int | None,
        rounds: int,
        warmup: int,
        backend: Literal["tracemalloc"],
    ) -> None:
        self.peak_below: int | None = peak_below
        self.slope_below: int | None = slope_below
        self.rounds_count: int = rounds
        self.warmup: int = warmup
        self._backend: MemoryBackend = self._build_backend(backend)
        self._active_context: Any = None
        self._growth_slope: float | None = None
        self._has_exited: bool = False
        self._peak_bytes: int | None = None
        self._retained_bytes_by_round: list[int] = []
        self._rounds_exhausted: bool = False
        self._rounds_touched: bool = False
        self._total_rounds: int = warmup + rounds
        self.rounds: MemoryRounds = MemoryRounds(self)

    def __enter__(self) -> Self:
        if memory_is_active():
            msg = "assert_memory blocks cannot be nested"
            raise BadRequestError(msg)
        if self.peak_below is None and self.slope_below is None:
            msg = "assert_memory requires peak_below or slope_below"
            raise BadRequestError(msg)
        if self.slope_below is not None and self.rounds_count < MIN_SLOPE_ROUNDS:
            msg = "assert_memory with slope_below requires rounds >= 10"
            raise BadRequestError(msg)
        self._active_context = mark_memory_active()
        self._active_context.__enter__()
        self._backend.start()
        self._backend.reset_peak()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is not None:
                return False
            self._finish_measurement()
            self._has_exited = True
            self._assert_budgets()
            append_memory_measurement(
                MemoryMeasurement(
                    growth_slope=self._growth_slope,
                    peak_budget=self.peak_below,
                    peak_bytes=self.peak_bytes,
                    rounds=self.rounds_count,
                    slope_budget=self.slope_below,
                )
            )
            return False
        finally:
            self._has_exited = True
            self._backend.stop()
            if self._active_context is not None:
                self._active_context.__exit__(exc_type, exc_value, traceback)
                self._active_context = None

    @property
    def growth_slope(self) -> float | None:
        if not self._has_exited:
            msg = "Memory growth slope was accessed before exiting the context manager"
            raise BadRequestError(msg)
        return self._growth_slope

    @property
    def peak_bytes(self) -> int:
        if not self._has_exited:
            msg = "Memory peak bytes were accessed before exiting the context manager"
            raise BadRequestError(msg)
        return cast("int", self._peak_bytes)

    @property
    def total_rounds(self) -> int:
        return self._total_rounds

    @staticmethod
    def _build_backend(backend: Literal["tracemalloc"]) -> MemoryBackend:
        if backend != "tracemalloc":
            msg = f"Unknown memory backend: {backend}"
            raise BadRequestError(msg)
        return TracemallocBackend()

    def mark_rounds_touched(self) -> None:
        self._rounds_touched = True

    def mark_rounds_exhausted(self) -> None:
        self._rounds_exhausted = True

    def record_completed_round(self, round_index: int) -> None:
        sample = self._backend.sample()
        if round_index < self.warmup:
            return
        self._retained_bytes_by_round.append(sample.retained_bytes)
        self._peak_bytes = max(self._peak_bytes or 0, sample.peak_bytes)

    def reset_round_peak(self) -> None:
        self._backend.reset_peak()

    def _finish_measurement(self) -> None:
        if self.rounds_count > 1 and not self._rounds_exhausted:
            msg = "assert_memory rounds must be fully consumed when rounds > 1"
            raise BadRequestError(msg)
        if not self._rounds_touched:
            sample = self._backend.sample()
            self._peak_bytes = sample.peak_bytes
            if self.rounds_count == 1:
                self._retained_bytes_by_round = [sample.retained_bytes]
        if self._peak_bytes is None:
            self._peak_bytes = 0
        self._growth_slope = self._calculate_growth_slope()

    def _calculate_growth_slope(self) -> float | None:
        if len(self._retained_bytes_by_round) < MIN_SLOPE_SAMPLE_COUNT:
            return None
        slopes: list[float] = []
        for start_index, start_bytes in enumerate(self._retained_bytes_by_round):
            slopes.extend(
                (self._retained_bytes_by_round[end_index] - start_bytes)
                / (end_index - start_index)
                for end_index in range(
                    start_index + 1, len(self._retained_bytes_by_round)
                )
            )
        return float(median(slopes))

    def _assert_budgets(self) -> None:
        if self.peak_below is not None and self.peak_bytes >= self.peak_below:
            message = f"memory peak {self.peak_bytes} >= {self.peak_below}"
            raise AssertionFailure(
                message,
                actual=self.peak_bytes,
                expected=self.peak_below,
                operator="<",
            )
        if self.slope_below is None:
            return
        if self._growth_slope is None:
            msg = "assert_memory could not calculate growth slope"
            raise BadRequestError(msg)
        if self._growth_slope >= self.slope_below:
            message = (
                f"memory growth slope {self._growth_slope:g} >= {self.slope_below}"
            )
            raise AssertionFailure(
                message,
                actual=self._growth_slope,
                expected=self.slope_below,
                operator="<",
            )


@overload
def assert_memory(
    *,
    peak_below: int,
    slope_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: Literal["tracemalloc"] = "tracemalloc",
) -> MemoryContext: ...
@overload
def assert_memory(
    *,
    slope_below: int,
    peak_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: Literal["tracemalloc"] = "tracemalloc",
) -> MemoryContext: ...
def assert_memory(
    *,
    peak_below: int | None = None,
    slope_below: int | None = None,
    rounds: int = 1,
    warmup: int = 1,
    backend: Literal["tracemalloc"] = "tracemalloc",
) -> MemoryContext:
    return MemoryContext(
        peak_below=peak_below,
        slope_below=slope_below,
        rounds=rounds,
        warmup=warmup,
        backend=backend,
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
