"""Async snektest examples."""

import asyncio
from collections.abc import AsyncGenerator

from hypothesis import Phase, settings
from hypothesis import strategies as st

from snektest import (
    assert_eq,
    assert_false,
    fixture,
    load_fixture,
    test,
    test_hypothesis,
)


async def fetch_username() -> str:
    """Pretend to call an async application boundary."""
    await asyncio.sleep(0)
    return "ada"


@test(mark="fast")
async def test_async_code() -> None:
    """Async tests await all work; the CLI accepts finite positive timeouts."""
    username = await fetch_username()
    assert_eq(username.upper(), "ADA")


def blocking_uppercase(value: str) -> str:
    """Represent a blocking application call that cannot run on the event loop."""
    return value.upper()


@test(mark="medium")
async def test_blocking_application_call() -> None:
    """Offload blocking work; an outer supervisor must bound its thread."""
    result = await asyncio.to_thread(blocking_uppercase, "snek")
    assert_eq(result, "SNEK")


async def serve_until_cancelled() -> None:
    """Represent background work owned by a fixture."""
    _ = await asyncio.Event().wait()


@fixture
async def background_server() -> AsyncGenerator[asyncio.Task[None]]:
    """Keep background work alive until fixture teardown."""
    server_task = asyncio.create_task(serve_until_cancelled())
    yield server_task
    assert_false(server_task.done())
    _ = server_task.cancel()
    _ = await asyncio.gather(server_task, return_exceptions=True)


@test(mark="fast")
async def test_fixture_owned_background_task() -> None:
    """Fixture-owned tasks are not classified as test leaks."""
    server_task = await load_fixture(background_server())
    await asyncio.sleep(0)
    assert_false(server_task.done())


@settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
@test_hypothesis(st.just("ada"), mark="fast")
async def test_async_property(username: str) -> None:
    """The CLI timeout covers the whole property run and cancels active awaits."""
    await asyncio.sleep(0)
    assert_eq(username.upper(), "ADA")
