"""Async snektest examples."""

import asyncio

from hypothesis import Phase, settings
from hypothesis import strategies as st

from snektest import assert_eq, test, test_hypothesis


async def fetch_username() -> str:
    """Pretend to call an async application boundary."""
    await asyncio.sleep(0)
    return "ada"


@test(mark="fast")
async def test_async_code() -> None:
    """Async tests await all work and have a 60-second CLI timeout by default."""
    username = await fetch_username()
    assert_eq(username.upper(), "ADA")


@settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
@test_hypothesis(st.just("ada"), mark="fast")
async def test_async_property(username: str) -> None:
    """The CLI timeout covers the whole property run and cancels active awaits."""
    await asyncio.sleep(0)
    assert_eq(username.upper(), "ADA")
