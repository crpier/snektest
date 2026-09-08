"""Meta tests for default async-hygiene checks."""

import asyncio
from textwrap import dedent

from snektest import Param, assert_false, assert_in, load_fixture, test
from snektest.assertions import assert_eq
from snektest.cli import run_tests_programmatic
from snektest.models import FilterItem
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
async def test_pending_task_fails_async_test() -> None:
    """An async test cannot pass while a spawned task remains pending."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from snektest import test

            @test()
            async def test_leaks_task() -> None:
                _ = asyncio.create_task(asyncio.Event().wait())
                await asyncio.sleep(0)
        """),
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)
    assert_eq(result["tests"][0]["exception"]["type"], "AssertionFailure")
    assert_eq(
        result["tests"][0]["exception"]["message"],
        "async test leaked 1 pending task",
    )


@test()
async def test_cancellation_resistant_task_reports_bounded_failure() -> None:
    """A leaked task cannot hang the CLI by suppressing cancellation."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio

            from snektest import test

            async def resist_cancellation() -> None:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        continue

            @test()
            async def test_leaks_resistant_task() -> None:
                _ = asyncio.create_task(resist_cancellation())
                await asyncio.sleep(0)
        """),
        name="test_resistant_task",
    )

    result = run_test_subprocess(test_file, "--timeout", "0.05", timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)
    assert_eq(result["tests"][0]["exception"]["type"], "AssertionFailure")
    assert_eq(
        result["tests"][0]["exception"]["message"],
        "async test leaked 1 pending task; 1 resisted cancellation for 0.05s",
    )


@test()
async def test_function_fixture_owns_background_task_through_teardown() -> None:
    """A function fixture can stop its background task during teardown."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import AsyncGenerator

            from snektest import Param, assert_false, assert_in, fixture, load_fixture, test

            async def serve() -> None:
                await asyncio.Event().wait()

            @fixture
            async def server() -> AsyncGenerator[asyncio.Task[None]]:
                task = asyncio.create_task(serve())
                yield task
                assert_false(task.done())
                task.cancel()
                _ = await asyncio.gather(task, return_exceptions=True)

            @test()
            async def test_uses_server() -> None:
                _ = await load_fixture(server())
                await asyncio.sleep(0)
        """),
        name="test_fixture_owned_task",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_eq(result["returncode"], 0)


@test()
async def test_session_fixture_owns_background_task_through_teardown() -> None:
    """A session fixture can stop its task after every test has finished."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import AsyncGenerator

            from snektest import Param, assert_false, assert_in, fixture, load_fixture, test

            async def serve() -> None:
                await asyncio.Event().wait()

            @fixture(scope="session")
            async def server() -> AsyncGenerator[asyncio.Task[None]]:
                task = asyncio.create_task(serve())
                yield task
                assert_false(task.done())
                task.cancel()
                _ = await asyncio.gather(task, return_exceptions=True)

            @test()
            async def test_uses_server_first() -> None:
                _ = await load_fixture(server())
                await asyncio.sleep(0)

            @test()
            async def test_uses_server_second() -> None:
                task = await load_fixture(server())
                assert_false(task.done())
        """),
        name="test_session_fixture_owned_task",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 2)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["session_teardown_failed"], 0)
    assert_eq(result["returncode"], 0)


@test()
async def test_session_fixture_task_leak_is_attributed_to_fixture() -> None:
    """A session fixture that abandons its task receives the teardown failure."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import AsyncGenerator

            from snektest import fixture, load_fixture, test

            async def serve() -> None:
                await asyncio.Event().wait()

            @fixture(scope="session")
            async def leaky_server() -> AsyncGenerator[asyncio.Task[None]]:
                task = asyncio.create_task(serve())
                yield task

            @test()
            async def test_uses_server() -> None:
                _ = await load_fixture(leaky_server())
        """),
        name="test_session_fixture_task_leak",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["session_teardown_failed"], 1)
    assert_eq(result["session_teardown_failures"][0]["fixture_name"], "leaky_server")
    assert_eq(
        result["session_teardown_failures"][0]["exception"]["type"],
        "FixtureTaskLeakError",
    )
    assert_eq(result["returncode"], 1)


@test()
async def test_embedded_run_does_not_cancel_new_host_task() -> None:
    """A task created by the host during a run remains host-owned."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    started_file = tmp_dir / "test-started"
    child_started_file = tmp_dir / "host-child-started"
    host_children: list[asyncio.Task[None]] = []

    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            import asyncio
            from pathlib import Path

            from snektest import test

            @test()
            async def test_waits_for_host_child() -> None:
                await asyncio.to_thread(Path({str(started_file)!r}).write_text, "")
                child_started = Path({str(child_started_file)!r})
                while not await asyncio.to_thread(child_started.exists):
                    await asyncio.sleep(0)
        """),
        name="test_embedded_host_task",
    )

    async def wait_forever() -> None:
        _ = await asyncio.Event().wait()

    async def create_host_child() -> None:
        while not await asyncio.to_thread(started_file.exists):  # noqa: ASYNC110
            await asyncio.sleep(0)
        child = asyncio.create_task(wait_forever())
        host_children.append(child)
        _ = await asyncio.to_thread(child_started_file.write_text, "")

    host_task = asyncio.create_task(create_host_child())
    child: asyncio.Task[None] | None = None
    try:
        summary = await run_tests_programmatic([FilterItem(str(test_file))])
        await host_task
        child = host_children[0]

        assert_eq(summary.passed, 1)
        assert_false(child.cancelled())
    finally:
        if child is not None and not child.done():
            _ = child.cancel()
            _ = await asyncio.gather(child, return_exceptions=True)
        if not host_task.done():
            _ = host_task.cancel()
            _ = await asyncio.gather(host_task, return_exceptions=True)


@test(
    [Param("test", "test"), Param("fixture", "fixture")],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_forced_finalizer_failure_is_contained(
    owner: str, worker_args: tuple[str, ...]
) -> None:
    """A finalizer error is attributed without preventing the next selected body."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        tmp_dir,
        dedent(f"""
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import FixtureError, fixture, load_fixture, test

            children: list[asyncio.Task[None]] = []

            async def child() -> None:
                try:
                    while True:
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            continue
                finally:
                    raise FixtureError("child finalizer failed")

            @fixture
            async def resource() -> AsyncGenerator[None]:
                children.extend([asyncio.create_task(child()), asyncio.create_task(child())])
                await asyncio.sleep(0)
                yield None

            @test(mark="fast")
            async def test_leaks() -> None:
                if {owner!r} == "fixture":
                    _ = await load_fixture(resource())
                else:
                    children.extend([asyncio.create_task(child()), asyncio.create_task(child())])
                    await asyncio.sleep(0)

            @test(mark="fast")
            def test_later() -> None:
                pass
        """),
        name="test_finalizer_failure",
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--timeout", "2", *worker_args, timeout=30
    )

    assert_eq(result["returncode"], 1)
    assert_eq(len(result["tests"]), 2)
    assert_eq(result["tests"][1]["status"], "passed")
    assert_in("child finalizer failed", str(result["tests"][0]))
    assert_eq(result["stderr"], "")
    diagnostics = result["tests"][0][
        "background_failures" if owner == "test" else "fixture_teardown_failures"
    ]
    assert_eq(len(diagnostics), 2 if owner == "test" else 3)
