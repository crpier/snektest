"""Memory-budget examples: peak allocation and leak detection."""

from snektest import assert_memory, test


@test(mark="fast")
def test_peak_allocation_budget() -> None:
    """Assert a region never allocates more than a peak budget (bytes as int)."""
    with assert_memory(peak_below=8 * 1024 * 1024):
        payload = bytearray(1024 * 1024)
        del payload


@test(mark="fast")
def test_no_leak_across_rounds() -> None:
    """Loop work over m.rounds and assert retained memory does not grow."""
    scratch: list[bytearray] = []
    with assert_memory(slope_below=64 * 1024, rounds=20) as measurement:
        for _ in measurement.rounds:
            scratch.clear()
            scratch.append(bytearray(32 * 1024))
    # Measurements stay readable after the block for custom assertions.
    _ = measurement.peak_bytes
    _ = measurement.growth_slope
