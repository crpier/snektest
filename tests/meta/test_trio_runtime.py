"""Real native-Trio programmatic lifetimes while CLI/worker wiring is in progress."""

lazy import json
lazy import subprocess
lazy import sys
lazy from pathlib import Path
lazy from textwrap import dedent
lazy from typing import Literal

lazy from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    assert_isinstance,
    temporary_directory,
    test,
)
lazy from testutils.helpers import create_test_file

_SCOPES: list[Param[Literal["function", "session", "run"]]] = [
    Param(value="function", name="function"),
    Param(value="session", name="session"),
    Param(value="run", name="run"),
]


_WORKERS: list[Param[int | None]] = [
    Param(value=None, name="local"),
    Param(value=1, name="worker"),
    Param(value=2, name="workers"),
]


def _run_native(
    path: Path, directory: Path, *, ceiling: float = 2, workers: int | None = None
) -> dict[str, object]:
    script = dedent(f"""
        import json
        import trio
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem
        async def main():
            result = await run_tests_programmatic([FilterItem({str(path)!r})], backend="trio", timeout={ceiling!r}, workers={workers!r})
            print(json.dumps({{"passed": result.passed, "failed": result.failed, "errors": result.errors,
                "fixture_errors": result.fixture_teardown_failed,
                "session_errors": result.session_teardown_failed, "run_errors": result.run_teardown_failed,
                "outputs": [item.captured_output for item in result.test_results],
                "details": repr(result)}}))
        trio.run(main)
    """)
    completed = subprocess.run(
        [sys.executable, "-X", "context_aware_warnings=1", "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    decoded = assert_isinstance(json.loads(completed.stdout), dict)
    return {assert_isinstance(key, str): value for key, value in decoded.items()}


@test(_SCOPES, _WORKERS, mark="slow")
def test_trio_fixture_scopes_keep_task_context_and_live_services(
    scope: Literal["function", "session", "run"],
    workers: int | None,
) -> None:
    directory = temporary_directory()
    trace = directory / "trace"
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        from collections.abc import AsyncGenerator
        from contextvars import ContextVar
        from pathlib import Path
        import anyio
        from anyio.abc import SocketAttribute, SocketStream
        import trio
        from snektest import assert_eq, assert_raises, fixture, load_fixture, test
        trace = Path({str(trace)!r})
        context = ContextVar("fixture-context", default="test")
        def record(message: str) -> None:
            with trace.open("a") as stream:
                _ = stream.write(message + "\\n")
        @fixture(scope={scope!r})
        async def resource() -> AsyncGenerator[str]:
            task = trio.lowlevel.current_task()
            token = context.set("fixture")
            with anyio.CancelScope():
                async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
                    async with anyio.create_task_group() as group:
                        async def serve(stream: SocketStream) -> None:
                            async with stream:
                                assert_eq(await stream.receive(4), b"ping")
                                await stream.send(b"pong")
                        group.start_soon(listener.serve, serve)
                        record("setup")
                        try:
                            yield str(listener.extra(SocketAttribute.local_port))
                        finally:
                            assert_eq(trio.lowlevel.current_task(), task)
                            assert_eq(context.get(), "fixture")
                            group.cancel_scope.cancel()
                            record("teardown")
            context.reset(token)
        async def check_service() -> None:
            port = int(await load_fixture(resource()))
            assert_eq(context.get(), "test")
            with assert_raises(RuntimeError):
                _ = asyncio.get_running_loop()
            async with await anyio.connect_tcp("127.0.0.1", port) as stream:
                await stream.send(b"ping")
                assert_eq(await stream.receive(4), b"pong")
        @test(mark="slow")
        async def test_first() -> None:
            await check_service()
        @test(mark="slow")
        async def test_second() -> None:
            await check_service()
    """),
    )
    result = _run_native(path, directory, workers=workers)
    assert_eq(result["passed"], 2, msg=str(result))
    assert_eq(
        [result[name] for name in ("fixture_errors", "session_errors", "run_errors")],
        [0, 0, 0],
        msg=str(result),
    )
    expected = (
        2 if scope == "function" else min(workers or 1, 2) if scope == "session" else 1
    )
    records = trace.read_text().splitlines()
    assert_eq(records.count("setup"), expected)
    assert_eq(records.count("teardown"), expected)


@test(mark="slow")
def test_trio_shared_session_setup_survives_cancelled_waiter() -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        import trio
        from snektest import assert_eq, assert_true, fixture, load_fixture, test
        ready = trio.Event()
        attempts = 0
        @fixture(scope="session")
        async def cached() -> AsyncGenerator[int]:
            global attempts
            attempts += 1
            await ready.wait()
            yield attempts
        @test(mark="fast")
        async def test_cancel_waiter() -> None:
            with trio.move_on_after(0.01) as scope:
                _ = await load_fixture(cached())
            assert_true(scope.cancelled_caught)
        @test(mark="fast")
        async def test_reuse() -> None:
            ready.set()
            assert_eq(await load_fixture(cached()), 1)
    """),
    )
    result = _run_native(path, directory)
    assert_eq(result["passed"], 2, msg=str(result))
    assert_eq(result["session_errors"], 0, msg=str(result))


@test(mark="slow")
def test_trio_body_and_cooperative_teardown_timeouts_are_reported() -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        import trio
        from snektest import fixture, load_fixture, test
        @fixture
        async def resource() -> AsyncGenerator[int]:
            with trio.CancelScope():
                yield 1
                await trio.sleep_forever()
        @test(mark="fast")
        async def test_timeout() -> None:
            await trio.sleep_forever()
        @test(mark="fast")
        async def test_teardown_timeout() -> None:
            _ = await load_fixture(resource())
        @test(mark="fast")
        async def test_next() -> None:
            pass
    """),
    )
    result = _run_native(path, directory, ceiling=0.03)
    assert_eq(result["errors"], 1, msg=str(result))
    assert_eq(result["fixture_errors"], 1, msg=str(result))
    assert_eq(result["passed"], 2, msg=str(result))
    assert_in("TestTimeoutError", str(result))
    assert_in("FixtureTeardownTimeoutError", str(result))


@test(
    [
        Param(value="setup", name="setup"),
        Param(value="teardown", name="teardown"),
        Param(value="task", name="native-descendant"),
    ],
    mark="slow",
)
def test_trio_resistance_aborts_before_next_case_without_force_closing(
    phase: str,
) -> None:
    directory = temporary_directory()
    release = directory / "release"
    next_case = directory / "next"
    cleaned = directory / "cleaned"
    path = create_test_file(
        directory,
        dedent(f"""
        from collections.abc import AsyncGenerator
        from contextvars import copy_context
        from pathlib import Path
        import trio
        from snektest import fixture, load_fixture, test
        release = Path({str(release)!r})
        cleaned = Path({str(cleaned)!r})
        async def resistant() -> None:
            with trio.CancelScope(shield=True):
                while not release.exists():
                    await trio.sleep(0.001)
            _ = cleaned.write_text("same-task cleanup")
        @fixture
        async def resource() -> AsyncGenerator[int]:
            if {phase!r} == "setup":
                await resistant()
            try:
                yield 1
            finally:
                if {phase!r} == "teardown":
                    await resistant()
        @test(mark="medium")
        async def test_resistant() -> None:
            if {phase!r} == "task":
                _ = trio.lowlevel.spawn_system_task(resistant, context=copy_context())
                await trio.sleep(0.002)
            else:
                _ = await load_fixture(resource())
        @test(mark="medium")
        async def test_must_not_start() -> None:
            _ = Path({str(next_case)!r}).write_text("unexpected")
    """),
    )
    script = dedent(f"""
        import json
        from pathlib import Path
        import trio
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem, RunInfrastructureError
        async def main():
            try:
                _ = await run_tests_programmatic([FilterItem({str(path)!r})], backend="trio", timeout=0.02)
            except RunInfrastructureError as error:
                print(json.dumps({{"message": str(error), "next": Path({str(next_case)!r}).exists(), "cleaned_early": Path({str(cleaned)!r}).exists()}}))
            finally:
                _ = Path({str(release)!r}).write_text("release only after abort")
            with trio.move_on_after(1):
                while not Path({str(cleaned)!r}).exists():
                    await trio.sleep(0.001)
        trio.run(main)
    """)
    completed = subprocess.run(
        [sys.executable, "-X", "context_aware_warnings=1", "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    result = json.loads(completed.stdout)
    assert_in("refusing to continue", result["message"])
    assert_false(result["next"])
    assert_false(result["cleaned_early"])
    assert_eq(cleaned.read_text(), "same-task cleanup")
