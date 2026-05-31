"""Async snektest examples."""

import asyncio

from snektest import assert_eq, test


async def fetch_username() -> str:
    """Pretend to call an async application boundary."""
    await asyncio.sleep(0)
    return "ada"


@test(mark="fast")
async def test_async_code() -> None:
    """Async tests can await application code directly."""
    username = await fetch_username()
    assert_eq(username.upper(), "ADA")
