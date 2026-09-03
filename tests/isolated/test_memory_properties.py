"""Generated configuration and ownership properties for `assert_memory`."""

from __future__ import annotations

import asyncio

from hypothesis import settings
from hypothesis import strategies as st

from snektest import (
    assert_eq,
    assert_false,
    assert_in,
    assert_memory,
    assert_raises,
)
from snektest.decorators import test_hypothesis
from snektest.models import BadRequestError

_MAX_MEMORY_ROUNDS = 1000
_LARGE_BUDGET = 1 << 60

_invalid_configurations = st.one_of(
    st.tuples(
        st.integers(min_value=-1_000_000, max_value=0),
        st.integers(min_value=0, max_value=10),
    ),
    st.tuples(
        st.integers(min_value=1, max_value=_MAX_MEMORY_ROUNDS),
        st.integers(min_value=-1_000_000, max_value=-1),
    ),
    st.tuples(
        st.integers(
            min_value=_MAX_MEMORY_ROUNDS + 1,
            max_value=1_000_000,
        ),
        st.integers(min_value=0, max_value=10),
    ),
)
_valid_configurations = st.tuples(
    st.integers(min_value=1, max_value=30),
    st.integers(min_value=0, max_value=5),
)


class _StopMeasurementError(Exception):
    """Stop after exercising setup or iteration without recording every example."""


@settings(deadline=None, max_examples=40)
@test_hypothesis(_invalid_configurations, mark="fast")
def test_invalid_memory_configurations_never_run_user_work(
    configuration: tuple[int, int],
) -> None:
    rounds, warmup = configuration
    body_ran = False

    with (
        assert_raises(BadRequestError),
        assert_memory(
            peak_below=_LARGE_BUDGET,
            rounds=rounds,
            warmup=warmup,
        ),
    ):
        body_ran = True

    assert_false(body_ran)


@settings(deadline=None, max_examples=30)
@test_hypothesis(_valid_configurations, mark="fast")
def test_valid_memory_configurations_yield_every_requested_iteration(
    configuration: tuple[int, int],
) -> None:
    rounds, warmup = configuration
    iteration_count = 0

    with (
        assert_raises(_StopMeasurementError),
        assert_memory(
            peak_below=_LARGE_BUDGET,
            rounds=rounds,
            warmup=warmup,
        ) as measurement,
    ):
        for _ in measurement.rounds:
            iteration_count += 1
        raise _StopMeasurementError

    assert_eq(iteration_count, warmup + rounds)


@settings(deadline=None, max_examples=20)
@test_hypothesis(st.integers(min_value=1, max_value=5), mark="fast")
async def test_concurrent_contenders_never_share_memory_measurement(
    contender_count: int,
) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_measurement() -> None:
        with (
            assert_raises(_StopMeasurementError),
            assert_memory(peak_below=_LARGE_BUDGET),
        ):
            first_entered.set()
            await release_first.wait()
            raise _StopMeasurementError

    first = asyncio.create_task(hold_measurement())
    await first_entered.wait()
    try:
        for _ in range(contender_count):
            with (
                assert_raises(BadRequestError) as raised,
                assert_memory(peak_below=_LARGE_BUDGET),
            ):
                pass
            assert_in("already active", str(raised.exception))
    finally:
        release_first.set()
        await first
