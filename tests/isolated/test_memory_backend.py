"""Unit tests for the tracemalloc memory backend behind `assert_memory`."""

import tracemalloc

from snektest import (
    assert_eq,
    assert_false,
    assert_ge,
    assert_is_not_none,
    assert_isinstance,
    assert_lt,
    assert_true,
    test,
)
from snektest.memory import TracemallocBackend, create_backend

_KB = 1024


@test()
def test_factory_builds_tracemalloc_backend() -> None:
    """The generic backend name resolves to the tracemalloc implementation."""
    _ = assert_isinstance(create_backend("tracemalloc"), TracemallocBackend)


@test()
def test_sample_reports_retained_above_baseline() -> None:
    """A live allocation shows up as retained bytes above the start baseline."""
    backend = TracemallocBackend()
    backend.start()
    try:
        payload = bytearray(500 * _KB)
        assert_ge(backend.sample().retained_bytes, 400 * _KB)
        del payload
    finally:
        backend.stop()


@test()
def test_reset_peak_drops_the_watermark() -> None:
    """After reset_peak, a small allocation's peak excludes an earlier spike."""
    backend = TracemallocBackend()
    backend.start()
    try:
        spike = bytearray(500 * _KB)
        del spike
        backend.reset_peak()
        residual = bytearray(_KB)
        assert_lt(backend.sample().peak_bytes, 400 * _KB)
        del residual
    finally:
        backend.stop()


@test()
def test_reset_peak_rebaselines_against_retained() -> None:
    """After reset_peak, peak excludes memory still retained across the reset.

    Unlike the freed-spike case, the 500KB here stays live through the reset;
    peak must be measured above that retained level, so a leak carried between
    rounds cannot inflate a later round's peak.
    """
    backend = TracemallocBackend()
    backend.start()
    try:
        retained = bytearray(500 * _KB)
        backend.reset_peak()
        transient = bytearray(_KB)
        del transient
        assert_lt(backend.sample().peak_bytes, 100 * _KB)
        del retained
    finally:
        backend.stop()


@test()
def test_owned_tracing_is_stopped() -> None:
    """A backend that started tracing stops it on stop()."""
    backend = TracemallocBackend()
    backend.start()
    backend.stop()
    assert_false(tracemalloc.is_tracing())


@test()
def test_borrowed_tracing_is_preserved() -> None:
    """Borrowing preserves caller ownership, traces, depth, and peak history."""
    tracemalloc.start(7)
    try:
        traced_payload = bytearray(_KB)
        trace_before = assert_is_not_none(
            tracemalloc.get_object_traceback(traced_payload)
        )
        spike = bytearray(500 * _KB)
        del spike
        peak_before = tracemalloc.get_traced_memory()[1]

        backend = TracemallocBackend()
        backend.start()
        backend.reset_peak()
        sample = backend.sample()
        backend.stop()

        assert_true(tracemalloc.is_tracing())
        assert_eq(tracemalloc.get_traceback_limit(), 7)
        assert_eq(
            tracemalloc.get_object_traceback(traced_payload),
            trace_before,
        )
        assert_ge(tracemalloc.get_traced_memory()[1], peak_before)
        assert_ge(sample.peak_bytes, 400 * _KB)
        del traced_payload
    finally:
        tracemalloc.stop()
