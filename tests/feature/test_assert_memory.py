import tracemalloc

from snektest import assert_eq, assert_lt, assert_memory, assert_raises, test
from snektest.models import AssertionFailure, BadRequestError

ONE_MEGABYTE = 1024 * 1024
LEAK_BYTES = 100_000


@test()
def test_assert_memory_peak_passes() -> None:
    with assert_memory(peak_below=10 * ONE_MEGABYTE) as memory:
        buffer = bytearray(ONE_MEGABYTE)
        assert_eq(len(buffer), ONE_MEGABYTE)

    assert_lt(memory.peak_bytes, 10 * ONE_MEGABYTE)


@test()
def test_assert_memory_peak_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:  # noqa: SIM117
        with assert_memory(peak_below=1):
            buffer = bytearray(ONE_MEGABYTE)
            assert_eq(len(buffer), ONE_MEGABYTE)

    assert_eq(exc_info.exception.operator, "<")
    assert_eq(exc_info.exception.expected, 1)


@test()
def test_assert_memory_growth_slope_passes_for_flat_body() -> None:
    with assert_memory(slope_below=LEAK_BYTES, rounds=10) as memory:
        for _ in memory.rounds:
            buffer = bytearray(LEAK_BYTES)
            assert_eq(len(buffer), LEAK_BYTES)

    assert_lt(memory.growth_slope, LEAK_BYTES)


@test()
def test_assert_memory_growth_slope_fails_for_retained_leak() -> None:
    leaked_buffers: list[bytearray] = []

    with assert_raises(AssertionFailure) as exc_info:  # noqa: SIM117
        with assert_memory(slope_below=LEAK_BYTES // 2, rounds=10) as memory:
            for _ in memory.rounds:
                leaked_buffers.append(bytearray(LEAK_BYTES))  # noqa: PERF401

    assert_eq(exc_info.exception.operator, "<")


@test()
def test_assert_memory_rejects_slope_with_too_few_rounds() -> None:
    with assert_raises(BadRequestError), assert_memory(slope_below=1, rounds=9):
        pass


@test()
def test_assert_memory_rejects_nested_blocks() -> None:
    with (  # noqa: SIM117
        assert_raises(BadRequestError),
        assert_memory(peak_below=ONE_MEGABYTE),
    ):
        with assert_memory(peak_below=ONE_MEGABYTE):
            pass


@test()
def test_assert_memory_rejects_unconsumed_rounds() -> None:
    with (
        assert_raises(BadRequestError),
        assert_memory(peak_below=ONE_MEGABYTE, rounds=2) as memory,
    ):
        assert_eq(memory.rounds_count, 2)


@test()
def test_assert_memory_peak_bytes_requires_exit() -> None:
    with (
        assert_raises(BadRequestError),
        assert_memory(peak_below=ONE_MEGABYTE) as memory,
    ):
        _ = memory.peak_bytes


@test()
def test_assert_memory_preserves_existing_tracemalloc_tracer() -> None:
    tracemalloc.start(1)
    try:
        with assert_memory(peak_below=ONE_MEGABYTE):
            buffer = bytearray(1024)
            assert_eq(len(buffer), 1024)

        assert_eq(tracemalloc.is_tracing(), True)
    finally:
        tracemalloc.stop()
