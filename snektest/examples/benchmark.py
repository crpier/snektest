"""Sync and async benchmark examples."""

import asyncio

from snektest import assert_benchmark, test


@test(mark="fast")
def test_list_copy_latency() -> None:
    """Assert a synchronous operation's median latency."""
    with assert_benchmark(
        name="list copy", median_below=0.01, rounds=20, warmup=3
    ) as timing:
        for _ in timing.rounds:
            _ = list(range(100))


@test(mark="fast")
async def test_async_checkpoint_latency() -> None:
    """Assert async latency directly on snektest's event loop."""
    with assert_benchmark(
        name="async checkpoint", median_below=0.01, rounds=20, warmup=3
    ) as timing:
        for _ in timing.rounds:
            await asyncio.sleep(0)
