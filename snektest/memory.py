"""Memory measurement backends and per-test measurement plumbing."""

import tracemalloc
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from snektest.models import MemoryMeasurement


@dataclass(frozen=True)
class Sample:
    """A point-in-time memory sample from a backend."""

    peak_bytes: int
    retained_bytes: int


class MemoryBackend(Protocol):
    """Backend seam for memory measurement engines."""

    def start(self) -> None:
        """Start measuring allocations."""

    def reset_peak(self) -> None:
        """Reset the backend peak watermark."""

    def sample(self) -> Sample:
        """Return retained and peak bytes since the backend baseline."""
        ...

    def stop(self) -> None:
        """Stop measuring allocations if this backend owns the active tracer."""


class TracemallocBackend:
    """Memory backend using Python's stdlib `tracemalloc` tracer."""

    def __init__(self) -> None:
        self._baseline_bytes: int = 0
        self._owns_tracer: bool = False
        self._started: bool = False

    def start(self) -> None:
        """Start tracemalloc and establish a baseline for later samples."""
        self._owns_tracer = not tracemalloc.is_tracing()
        if self._owns_tracer:
            tracemalloc.start(1)
        self._baseline_bytes = tracemalloc.get_traced_memory()[0]
        self._started = True

    def reset_peak(self) -> None:
        """Reset tracemalloc's peak watermark."""
        tracemalloc.reset_peak()

    def sample(self) -> Sample:
        """Return retained and peak traced bytes above the entry baseline."""
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        return Sample(
            peak_bytes=max(0, peak_bytes - self._baseline_bytes),
            retained_bytes=current_bytes - self._baseline_bytes,
        )

    def stop(self) -> None:
        """Stop tracemalloc only when this backend started it."""
        if self._started and self._owns_tracer:
            tracemalloc.stop()
        self._started = False


_memory_active: ContextVar[bool] = ContextVar("snektest_memory_active", default=False)
_memory_measurements: ContextVar[list[MemoryMeasurement] | None] = ContextVar(
    "snektest_memory_measurements",
    default=None,
)


def memory_is_active() -> bool:
    """Return whether an `assert_memory` block is active in this context."""
    return _memory_active.get()


@contextmanager
def mark_memory_active() -> Generator[None]:
    """Mark an active memory assertion and restore the prior state on exit."""
    token = _memory_active.set(True)
    try:
        yield
    finally:
        _memory_active.reset(token)


def append_memory_measurement(measurement: MemoryMeasurement) -> None:
    """Append a passed measurement to the current test's sink, if present."""
    measurements = _memory_measurements.get()
    if measurements is not None:
        measurements.append(measurement)


@contextmanager
def collect_memory_measurements() -> Generator[list[MemoryMeasurement]]:
    """Collect memory measurements emitted during one test body."""
    measurements: list[MemoryMeasurement] = []
    token = _memory_measurements.set(measurements)
    try:
        yield measurements
    finally:
        _memory_measurements.reset(token)
