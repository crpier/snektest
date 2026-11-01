"""Asyncio-specific snektest examples; use --example anyio for both backends.

Cleanup follows cancellation-created descendants under one owner deadline, leaving
unrelated tasks alone. Unreapable owned tasks stop the run before another test.
Failed fixture setup retains its task owner until scope teardown, where cleanup
failures accompany the original setup error. Retries retain each attempt's cleanup.
Shared async session setup survives cancellation of a test's waiter. At scope
teardown, unfinished setup receives bounded cancellation and retains cleanup
errors as fixture teardown diagnostics. Function setup still pending when its
test returns is abandoned fixture work, cleaned before the next test.

Hypothesis cancellation stops new examples, releases cross-thread handoffs and
bounds resistant async-example cleanup. Blocking thread work needs an outer timeout.
Handoffs copy per-example ContextVars and bind Hypothesis controls during each
coroutine step. note/event/target and st.data().draw work in the example task
and directly awaited helpers, not independently spawned tasks or async fixtures.

Thread and unraisable failures remain errors even if their message formatter fails.

Forced task-finalizer errors become task_cleanup background diagnostics or
fixture teardown failures; they do not abort later cleanup or test execution.
"""

lazy import asyncio
lazy from collections.abc import AsyncGenerator

lazy from hypothesis import Phase, event, note, settings, target
lazy from hypothesis import strategies as st

lazy from snektest import (
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
    try:
        # Protect setup before yielding as well as normal fixture teardown.
        await asyncio.sleep(0)
        yield server_task
    finally:
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
@test_hypothesis(st.just("ada"), st.data(), mark="fast")
async def test_async_property(username: str, data: st.DataObject) -> None:
    """Controls work after awaits without leaking into unrelated loop tasks."""
    await asyncio.sleep(0)
    suffix = data.draw(st.just("!"), label="suffix")
    event("async example")
    target(float(len(username)))
    note(f"username: {username}")
    assert_eq(username.upper() + suffix, "ADA!")
