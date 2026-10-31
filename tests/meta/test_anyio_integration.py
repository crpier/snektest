"""AnyIO's asyncio integration through real local and process CLI runs."""

import os
import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_completed_anyio_offload_does_not_leak_its_idle_pool_worker(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from snektest import assert_eq, test

        @test(mark="medium")
        async def test_completed_offload() -> None:
            value = await anyio.to_thread.run_sync(lambda: 42)
            assert_eq(value, 42)
        """),
        name="test_completed_anyio_offload",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["tests"][0].get("background_failures", []), [])


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_an_idle_anyio_worker_is_recognized_even_after_renaming(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        import threading
        from snektest import test

        def rename_worker() -> None:
            threading.current_thread().name = "application-owned-pool-label"

        @test(mark="medium")
        async def test_offload() -> None:
            await anyio.to_thread.run_sync(rename_worker)
        """),
        name="test_renamed_anyio_worker",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_a_raw_thread_named_like_anyio_is_still_a_leak(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        import threading
        from snektest import test

        stop = threading.Event()
        raw: threading.Thread | None = None

        @test(mark="medium")
        async def test_lookalike() -> None:
            global raw
            await anyio.to_thread.run_sync(lambda: None)
            raw = threading.Thread(name="AnyIO worker thread", target=stop.wait, args=(5,))
            raw.start()

        @test(mark="medium")
        def test_release() -> None:
            stop.set()
            if raw is not None:
                raw.join()
        """),
        name="test_anyio_lookalike_thread",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq((result["failed"], result["passed"]), (1, 1))
    background = result["tests"][0]["background_failures"]
    assert_eq(len(background), 1)
    assert_eq(background[0]["origin"], "thread_leak")
    assert_eq(background[0]["label"], "AnyIO worker thread")


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_new_busy_abandoned_anyio_work_is_not_exempted(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        import threading
        from snektest import test

        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def blocking_work() -> None:
            entered.set()
            _ = release.wait(5)
            finished.set()

        @test(mark="medium")
        async def test_abandons_work() -> None:
            scopes: list[anyio.CancelScope] = []
            async def offload() -> None:
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await anyio.to_thread.run_sync(blocking_work, abandon_on_cancel=True)
            async with anyio.create_task_group() as group:
                group.start_soon(offload)
                while not entered.is_set():
                    await anyio.sleep(0)
                scopes[0].cancel()

        @test(mark="medium")
        async def test_release() -> None:
            release.set()
            while not finished.is_set():
                await anyio.sleep(0)
        """),
        name="test_abandoned_anyio_work",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq((result["failed"], result["passed"]), (1, 1))
    background = result["tests"][0]["background_failures"]
    assert_eq(len(background), 1)
    assert_eq(background[0]["origin"], "thread_leak")


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_anyio_tasks_and_offloads_work_in_tests_and_fixture_phases(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, assert_not_in, assert_raises, fixture, load_fixture, test

        events: list[str] = []

        @fixture(scope="session")
        async def session() -> AsyncGenerator[str]:
            value = await anyio.to_thread.run_sync(lambda: "session")
            try:
                yield value
            finally:
                await anyio.to_thread.run_sync(events.append, "session-close")
                print("session closed")

        @fixture
        async def resource() -> AsyncGenerator[str]:
            value = await anyio.to_thread.run_sync(lambda: "function")
            try:
                yield value
            finally:
                await anyio.to_thread.run_sync(events.append, "function-close")

        def broken_work() -> None:
            raise ValueError("offload error")

        async def append(value: str) -> None:
            await anyio.sleep(0)
            events.append(value)

        @test(mark="medium")
        async def test_operations() -> None:
            assert_eq(await load_fixture(session()), "session")
            assert_eq(await load_fixture(resource()), "function")
            async with anyio.create_task_group() as group:
                group.start_soon(append, "one")
                group.start_soon(append, "two")
            assert_eq(sorted(events), ["one", "two"])
            with assert_raises(ValueError, match="offload error"):
                await anyio.to_thread.run_sync(broken_work)
            with anyio.CancelScope() as scope:
                scope.cancel()
                await anyio.sleep(0)
            assert_not_in("session-close", events)

        @test(mark="medium")
        def test_after_teardown() -> None:
            assert_eq(events.count("function-close"), 1)
            assert_not_in("session-close", events)
        """),
        name="test_anyio_fixture_operations",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 2)
    assert_eq(result["session_teardown_failed"], 0)
    assert_eq(result["session_teardown_output"], "session closed\n")


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_core_execution_does_not_import_anyio(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        import sys
        from snektest import assert_false, test

        @test(mark="medium")
        async def test_asyncio_only() -> None:
            await asyncio.to_thread(lambda: None)

        @test(mark="fast")
        def test_after_observation() -> None:
            assert_false("anyio" in sys.modules)
        """),
        name="test_anyio_is_not_imported",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 2)


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_task_local_anyio_scope_can_span_async_fixture_yield(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test

        @fixture
        async def scoped() -> AsyncGenerator[None]:
            with anyio.CancelScope():
                yield

        @test(mark="fast")
        async def test_supported_scope() -> None:
            await load_fixture(scoped())
        """),
        name="test_task_local_anyio_fixture_scope",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_eq(result["passed"], 1)


@test([Param(value=None, name="local"), Param(value=2, name="workers")], mark="slow")
def test_anyio_console_run_succeeds(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from snektest import Param, assert_eq, test

        @test([Param(value=i, name=str(i)) for i in range(4)], mark="medium")
        async def test_offload(value: int) -> None:
            assert_eq(await anyio.to_thread.run_sync(lambda: value), value)
        """),
        name="test_anyio_console",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("4 passed", completed.stdout)


@test(mark="slow")
def test_programmatic_run_leaves_its_anyio_host_pool_usable() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        import threading
        from snektest import test

        @test(mark="medium")
        async def test_offload() -> None:
            worker = await anyio.to_thread.run_sync(threading.current_thread)
            print(id(worker))
        """),
        name="test_anyio_embedding",
    )
    source = dedent(f"""
        import anyio
        import threading
        from snektest import assert_eq
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem

        async def main() -> None:
            result = await run_tests_programmatic([FilterItem({str(path)!r})])
            assert_eq(result.passed, 1)
            original_worker = int(result.test_results[0].captured_output)
            current_worker = await anyio.to_thread.run_sync(threading.current_thread)
            assert_eq(id(current_worker), original_worker)
            print("host pool remains usable")

        anyio.run(main, backend="asyncio")
    """)
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stdout, "host pool remains usable\n")


@test([Param(value=None, name="local"), Param(value=1, name="worker")], mark="slow")
def test_anyio_group_errors_remain_normal_structured_errors(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from snektest import test

        def broken_work() -> None:
            raise ValueError("application failure")

        @test(mark="medium")
        async def test_group_error() -> None:
            async with anyio.create_task_group() as group:
                group.start_soon(anyio.to_thread.run_sync, broken_work)
        """),
        name="test_anyio_group_error",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 1)
    error = result["tests"][0]["exception"]
    assert_eq(error["type"], "ExceptionGroup")
    assert_eq(error["exceptions"][0]["type"], "ValueError")
    assert_eq(error["exceptions"][0]["message"], "application failure")
    assert_eq(result["tests"][0].get("background_failures", []), [])
