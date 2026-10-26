"""AnyIO operations on Snektest's existing asyncio runner.

Install snektest[anyio]. Await operations directly, without anyio.run() in tests.
Task-local cancel scopes/task groups belong wholly inside one task: never keep
one open across fixture yield, since setup and teardown may use different tasks.
Idle registered pool workers are safe; no thread-name exemption or prewarming
is needed. Blocking threads cannot be stopped, so retain an outer timeout.
There is no Trio backend or runtime selector in this integration slice.
"""

lazy from collections.abc import AsyncGenerator

lazy import anyio
lazy import anyio.lowlevel
lazy from anyio.to_thread import run_sync

lazy from snektest import assert_eq, fixture, load_fixture, test


@fixture
async def configuration() -> AsyncGenerator[str]:
    """Await operations independently in setup and teardown."""
    value = await run_sync(lambda: "ready")
    try:
        yield value
    finally:
        # A scope opened here must also close here, not in the setup phase.
        await anyio.lowlevel.checkpoint()


@test(mark="medium")
async def test_anyio_fixture_operations() -> None:
    config = await load_fixture(configuration())
    assert_eq(config, "ready")


@test(mark="fast")
async def test_anyio_task_group() -> None:
    values: list[int] = []

    async def collect(value: int) -> None:
        await anyio.lowlevel.checkpoint()
        values.append(value)

    # Both scopes close in the test's own task before its body returns.
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(collect, 1)
            group.start_soon(collect, 2)
    assert_eq(sorted(values), [1, 2])
