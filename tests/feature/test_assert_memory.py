"""Behavior of the `assert_memory` context-manager assertion.

Allocations are kept large (hundreds of KB to ~1MB) so budgets dominate
allocator/GC noise and the leak-vs-flat verdicts stay deterministic.
"""

from snektest import assert_ge, assert_lt, assert_memory, assert_raises, test
from snektest.models import AssertionFailure, BadRequestError

_MB = 1024 * 1024


@test(mark="fast")
async def test_peak_under_budget_passes() -> None:
    """A transient 1MB allocation stays under a generous peak budget."""
    with assert_memory(peak_below=8 * _MB) as measurement:
        payload = bytearray(_MB)
        del payload
    assert_lt(measurement.peak_bytes, 8 * _MB)


@test(mark="fast")
async def test_peak_over_budget_fails() -> None:
    """A 1MB allocation blows a 64KB peak budget on exit."""
    with (
        assert_raises(AssertionFailure) as failure,
        assert_memory(peak_below=64 * 1024),
    ):
        _payload = bytearray(_MB)

    assert_ge(failure.exception.actual, _MB)
    assert_lt(failure.exception.expected, _MB)


@test(mark="fast")
async def test_monotonic_growth_fails_slope_budget() -> None:
    """Retaining ~200KB each round produces a slope well past the budget."""
    with (
        assert_raises(AssertionFailure),
        assert_memory(slope_below=1024, rounds=15) as measurement,
    ):
        _retained = [bytearray(200 * 1024) for _ in measurement.rounds]


@test(mark="fast")
async def test_flat_usage_passes_slope_budget() -> None:
    """Re-using a single buffer each round keeps growth near zero."""
    scratch: list[bytearray] = []
    with assert_memory(slope_below=100 * 1024, rounds=15) as measurement:
        for _ in measurement.rounds:
            scratch.clear()
            scratch.append(bytearray(50 * 1024))
    assert_lt(measurement.growth_slope, 100 * 1024)


@test(mark="fast")
async def test_leak_does_not_inflate_per_round_peak() -> None:
    """A leak retained across rounds must not inflate the per-round peak.

    Each round appends 128KB to a list held for the whole block; peak is reset
    per round, so the reported peak is one round's allocation (~128KB), well
    under a 300KB budget, even though 12 rounds retain ~1.5MB in total.
    """
    with assert_memory(peak_below=300 * 1024, rounds=12) as measurement:
        _leaked = [bytearray(128 * 1024) for _ in measurement.rounds]
    assert_lt(measurement.peak_bytes, 300 * 1024)


@test(mark="fast")
async def test_leak_still_caught_by_slope_budget() -> None:
    """The same leak that no longer inflates peak still trips the slope budget."""
    with (
        assert_raises(AssertionFailure),
        assert_memory(slope_below=1024, rounds=12) as measurement,
    ):
        _leaked = [bytearray(128 * 1024) for _ in measurement.rounds]


@test(mark="fast")
async def test_flat_body_slope_stays_near_zero() -> None:
    """An empty round body must not manufacture growth from measurement machinery.

    The retained-samples list is preallocated and only plain ints are kept per
    round, so a flat body reports a near-zero slope rather than the machinery's
    own per-round allocation.
    """
    with assert_memory(slope_below=64, rounds=30) as measurement:
        for _ in measurement.rounds:
            pass
    assert_lt(measurement.growth_slope, 64)


@test(mark="fast")
async def test_slope_budget_requires_ten_rounds() -> None:
    """A slope budget under ten rounds has no meaningful fit and is rejected."""
    with assert_raises(BadRequestError), assert_memory(slope_below=1024, rounds=3):
        pass


@test(mark="fast")
async def test_nesting_is_rejected() -> None:
    """A second measurement inside an active one corrupts the outer watermark."""
    with (
        assert_raises(BadRequestError),
        assert_memory(peak_below=_MB),
        assert_memory(peak_below=_MB),
    ):
        pass


@test(mark="fast")
async def test_unconsumed_rounds_iterator_is_rejected() -> None:
    """Asking for multiple rounds but never looping `m.rounds` is misuse."""
    with assert_raises(BadRequestError), assert_memory(peak_below=8 * _MB, rounds=12):
        pass


@test(mark="fast")
async def test_partial_rounds_iterator_is_rejected() -> None:
    """Breaking out of the rounds loop early leaves an incomplete fit."""
    with (
        assert_raises(BadRequestError),
        assert_memory(peak_below=8 * _MB, rounds=12) as measurement,
    ):
        for _ in measurement.rounds:
            break


@test(mark="fast")
async def test_peak_bytes_before_exit_is_rejected() -> None:
    """Reading the measurement before the block closes has no value yet."""
    with (
        assert_raises(BadRequestError),
        assert_memory(peak_below=8 * _MB) as measurement,
    ):
        _ = measurement.peak_bytes


@test(mark="fast")
async def test_custom_assert_reads_peak_bytes() -> None:
    """The measurement stays readable after exit for custom assertions."""
    with assert_memory(peak_below=8 * _MB) as measurement:
        payload = bytearray(_MB)
        del payload

    assert_ge(measurement.peak_bytes, _MB // 2)
