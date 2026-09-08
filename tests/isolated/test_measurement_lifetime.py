"""Measurement contexts cannot re-emit samples from an earlier lifetime."""

from contextlib import nullcontext
from gc import disable, enable, isenabled
from tracemalloc import get_traceback_limit, is_tracing, start, stop

from snektest import (
    AssertionFailure,
    BadRequestError,
    FixtureError,
    Param,
    assert_eq,
    assert_false,
    assert_is_not_none,
    assert_memory,
    assert_raises,
    test,
)
from snektest.assertions import MemoryContext
from snektest.benchmark import BenchmarkContext, collect_benchmarks
from snektest.memory import collect_measurements


def _context(
    kind: str, *, budget_failure: bool = False
) -> BenchmarkContext | MemoryContext:
    if kind == "memory":
        return assert_memory(
            peak_below=0 if budget_failure else 100_000_000, rounds=2, warmup=2
        )
    return BenchmarkContext(
        median_below=1e-12 if budget_failure else 1,
        rounds=2,
        warmup=2,
        disable_gc=True,
        clock=iter(range(100)).__next__,
    )


@test(
    [Param("benchmark", "benchmark"), Param("memory", "memory")],
    [Param(outcome, outcome) for outcome in ("pass", "budget", "body", "partial")],
    mark="fast",
)
def test_measurement_rejects_reentry(kind: str, outcome: str) -> None:
    """Successful and unsuccessful exits both consume the context exactly once."""
    region = _context(kind, budget_failure=outcome == "budget")
    expected_error = {
        "budget": AssertionFailure,
        "body": FixtureError,
        "partial": BadRequestError,
    }.get(outcome)
    entered_again = False
    with collect_benchmarks() as timing, collect_measurements() as memory:
        with (
            assert_raises(expected_error)
            if expected_error is not None
            else nullcontext(),
            region,
        ):
            for _ in region.rounds:
                if outcome == "body":
                    message = "body failed"
                    raise FixtureError(message)
                if outcome == "partial":
                    break
        count = (len(timing.measurements), len(memory))
        with assert_raises(BadRequestError), region:
            entered_again = True
            for _ in region.rounds:
                pass
        assert_eq((len(timing.measurements), len(memory)), count)
    assert_false(entered_again)


@test([Param("benchmark", "benchmark"), Param("memory", "memory")], mark="fast")
def test_measurement_rounds_require_active_context(kind: str) -> None:
    """Access before entry cannot start measurement or mutate process state."""
    region = _context(kind)
    with assert_raises(BadRequestError):
        _ = region.rounds


@test([Param("benchmark", "benchmark"), Param("memory", "memory")], mark="fast")
def test_saved_rounds_cannot_resume_after_exit(kind: str) -> None:
    """A retained iterator cannot sample or change GC after cleanup restored it."""
    region = _context(kind)
    iterator = None
    with assert_raises(BadRequestError), region:
        iterator = region.rounds
        _ = next(iterator)
    saved = assert_is_not_none(iterator)
    with assert_raises(BadRequestError):
        _ = next(saved)


@test(mark="fast")
def test_reentry_preserves_borrowed_tracing() -> None:
    """A rejected memory context cannot stop tracing owned by its caller."""
    tracing = is_tracing()
    if not tracing:
        start(3)
    depth = get_traceback_limit()
    try:
        region = _context("memory")
        with region:
            for _ in region.rounds:
                pass
        with assert_raises(BadRequestError), region:
            pass
        assert_eq((is_tracing(), get_traceback_limit()), (True, depth))
    finally:
        if not tracing:
            stop()


@test(mark="fast")
def test_reentry_preserves_disabled_gc() -> None:
    """A rejected benchmark context cannot enable caller-disabled GC."""
    enabled = isenabled()
    disable()
    try:
        region = _context("benchmark")
        with region:
            for _ in region.rounds:
                pass
        with assert_raises(BadRequestError), region:
            pass
        assert_false(isenabled())
    finally:
        if enabled:
            enable()
