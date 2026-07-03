"""Memory assertion examples."""

from snektest import assert_lt, assert_memory, test

ONE_MEGABYTE = 1024 * 1024


@test(mark="fast")
def test_peak_allocation_budget() -> None:
    """Assert a block stays under a peak allocation budget."""
    with assert_memory(peak_below=10 * ONE_MEGABYTE) as memory:
        buffer = bytearray(ONE_MEGABYTE)
        assert_lt(len(buffer), 2 * ONE_MEGABYTE)

    assert_lt(memory.peak_bytes, 10 * ONE_MEGABYTE)


@test(mark="fast")
def test_no_retained_growth() -> None:
    """Assert repeated work has a flat retained-memory slope."""
    with assert_memory(slope_below=50_000, rounds=10) as memory:
        for _ in memory.rounds:
            buffer = bytearray(100_000)
            assert_lt(len(buffer), 200_000)

    assert_lt(memory.growth_slope, 50_000)
