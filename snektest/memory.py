"""Memory-measurement backends for `assert_memory`.

A backend is a thread-inclusive tracer seam. It must not assume a synchronous
`int` return: the future memray backend captures allocations to a file that is
read back after the fact, and allocation-heavy work often runs in executor
threads. Only `TracemallocBackend` is implemented today; memray slots in later
behind the same `MemoryBackend` protocol without touching call sites.

The module also owns two run-scoped context variables that `assert_memory`
leans on: a nesting guard (a second `assert_memory` inside an active one would
corrupt the outer peak watermark) and a measurement sink drained into the test
result on success.
"""

import tracemalloc
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal, Protocol

from snektest.models import BadRequestError, MemoryMeasurement

type BackendName = Literal["tracemalloc"]


@dataclass(frozen=True)
class Sample:
    """One point-in-time memory reading, relative to the backend baseline.

    `retained_bytes` is live memory still held at sample time, measured above
    the baseline captured when the backend started. `peak_bytes` is the
    high-water mark since the last `reset_peak()`, measured above the retained
    total at that reset, so a leak retained across rounds does not inflate it.
    Both subtract the tracer's own bookkeeping so it does not leak into the
    numbers.
    """

    retained_bytes: int
    peak_bytes: int


class MemoryBackend(Protocol):
    """Thread-inclusive memory tracer used by `assert_memory`.

    `start()` establishes a baseline; `reset_peak()` drops the peak watermark to
    the current level and re-baselines the peak against it, so each round's peak
    measures only that round's transient allocation and a leak retained from
    earlier rounds cannot inflate it; `sample()` reads retained bytes above the
    start baseline and peak bytes above the last `reset_peak()`; `stop()` undoes
    `start()`, never tearing down tracing the backend did not itself begin.
    """

    def start(self) -> None: ...
    def reset_peak(self) -> None: ...
    def sample(self) -> Sample: ...
    def stop(self) -> None: ...


class TracemallocBackend:
    """`MemoryBackend` over the stdlib `tracemalloc` tracer.

    On `start()`, if tracing is already active the backend records that it does
    not own it and baselines off the current traced total; otherwise it starts
    tracing (frame depth 1, totals only) and baselines afterwards so
    tracemalloc's own allocations are subtracted out. `stop()` stops tracing
    only when the backend started it, leaving a caller's pre-existing tracing
    session untouched.

    `reset_peak()` re-baselines the peak against the current retained total, so
    `sample().peak_bytes` reports the peak above the *most recent* reset rather
    than above the start baseline. In rounds mode this makes each round's peak
    its own transient allocation; in whole-block mode the single reset at
    `__enter__` sits at the start baseline, preserving whole-block semantics.
    """

    def __init__(self) -> None:
        self._owns_tracing: bool = False
        self._baseline_bytes: int = 0
        self._peak_baseline_bytes: int = 0

    def start(self) -> None:
        if tracemalloc.is_tracing():
            self._owns_tracing = False
        else:
            tracemalloc.start(1)
            self._owns_tracing = True
        self._baseline_bytes = tracemalloc.get_traced_memory()[0]
        self._peak_baseline_bytes = self._baseline_bytes

    def reset_peak(self) -> None:
        tracemalloc.reset_peak()
        self._peak_baseline_bytes = tracemalloc.get_traced_memory()[0]

    def sample(self) -> Sample:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        return Sample(
            retained_bytes=current_bytes - self._baseline_bytes,
            peak_bytes=max(0, peak_bytes - self._peak_baseline_bytes),
        )

    def stop(self) -> None:
        if self._owns_tracing:
            tracemalloc.stop()


def create_backend(name: BackendName) -> MemoryBackend:
    """Build the memory backend named by `assert_memory(backend=...)`.

    Only `"tracemalloc"` exists today; the seam keeps call sites stable when
    memray lands.
    """
    if name == "tracemalloc":
        return TracemallocBackend()
    msg = f"Unknown memory backend: {name!r}"
    raise BadRequestError(msg)


_measurement_active: ContextVar[bool] = ContextVar(
    "snektest_memory_measurement_active", default=False
)
_measurement_sink: ContextVar[list[MemoryMeasurement] | None] = ContextVar(
    "snektest_memory_measurement_sink", default=None
)


def is_measurement_active() -> bool:
    """Whether an `assert_memory` block is currently open on this context."""
    return _measurement_active.get()


def enter_measurement() -> Token[bool]:
    """Mark a measurement as active, returning the token to reset it on exit."""
    return _measurement_active.set(True)


def exit_measurement(token: Token[bool]) -> None:
    """Clear the active-measurement flag set by `enter_measurement`."""
    _measurement_active.reset(token)


def record_measurement(measurement: MemoryMeasurement) -> None:
    """Append a passing measurement to the run's sink, if one is bound.

    No sink is bound outside a test body (e.g. `assert_memory` used in a
    standalone script), in which case the measurement is simply not collected.
    """
    sink = _measurement_sink.get()
    if sink is not None:
        sink.append(measurement)


@contextmanager
def collect_measurements() -> Generator[list[MemoryMeasurement]]:
    """Bind a fresh measurement sink for the duration of a test body."""
    measurements: list[MemoryMeasurement] = []
    token = _measurement_sink.set(measurements)
    try:
        yield measurements
    finally:
        _measurement_sink.reset(token)
