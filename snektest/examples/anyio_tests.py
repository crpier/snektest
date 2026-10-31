"""AnyIO operations and fixture-owned services on either native backend.

Install snektest[anyio] for default asyncio, or snektest[anyio,trio] for Trio.
Save this example to test_anyio.py, then run:
    snektest test_anyio.py
    snektest --backend trio test_anyio.py
Await APIs directly without nesting anyio.run().
Managed async fixtures keep setup/yield/teardown in one task. Task-local scopes
may span yield, but govern fixture work rather than the test; yield resources
explicitly instead of relying on fixture ContextVar changes reaching the test.
Stop background services in finally. Threads still require an outer timeout.
The choice is command-wide, including workers. Resistant Trio cleanup aborts
before the next case without force-closing tasks; shutdown can need an outer
deadline. Asyncio-specific libraries still require the asyncio backend.
"""

lazy from collections.abc import AsyncGenerator

lazy import anyio
lazy import anyio.lowlevel
lazy from anyio.abc import ObjectReceiveStream, ObjectSendStream
lazy from anyio.to_thread import run_sync

lazy from snektest import assert_eq, fixture, load_fixture, test


@fixture
async def uppercase_service() -> AsyncGenerator[
    tuple[ObjectSendStream[str], ObjectReceiveStream[str]]
]:
    """Keep a request/reply service alive while the test uses its streams."""
    requests, request_receiver = anyio.create_memory_object_stream[str]()
    replies, reply_receiver = anyio.create_memory_object_stream[str]()

    async def serve() -> None:
        async with request_receiver, replies:
            async for value in request_receiver:
                await replies.send(await run_sync(str.upper, value))

    async with requests, reply_receiver, anyio.create_task_group() as group:
        group.start_soon(serve)
        try:
            yield requests, reply_receiver
        finally:
            group.cancel_scope.cancel()


@test(mark="medium")
async def test_anyio_fixture_operations() -> None:
    requests, replies = await load_fixture(uppercase_service())
    await requests.send("ready")
    assert_eq(await replies.receive(), "READY")


@test(mark="fast")
async def test_anyio_task_group() -> None:
    values: list[int] = []

    async def collect(value: int) -> None:
        await anyio.lowlevel.checkpoint()
        values.append(value)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(collect, 1)
            group.start_soon(collect, 2)
    assert_eq(sorted(values), [1, 2])
