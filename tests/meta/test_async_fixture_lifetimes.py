"""Async fixture task lifetimes through complete local and spawned CLI runs."""

import json
import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [Param(value=scope, name=scope) for scope in ("function", "session", "run")],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_async_fixture_owns_its_task_group_across_yield(
    scope: str, arguments: tuple[str, ...]
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        import anyio
        from collections.abc import AsyncGenerator
        from contextvars import ContextVar
        from snektest import assert_eq, assert_is, fixture, load_fixture, test

        label: ContextVar[str] = ContextVar("label", default="test")
        setups = 0

        @fixture(scope={scope!r})
        async def resource() -> AsyncGenerator[int]:
            global setups
            setups += 1
            owner = asyncio.current_task()
            token = label.set("fixture")
            try:
                async with anyio.create_task_group() as group:
                    group.start_soon(anyio.sleep_forever)
                    try:
                        yield setups
                    finally:
                        assert_is(asyncio.current_task(), owner)
                        assert_eq(label.get(), "fixture")
                        group.cancel_scope.cancel()
            finally:
                label.reset(token)
                print("closed")

        @test(mark="fast")
        async def test_first() -> None:
            assert_eq(await load_fixture(resource()), 1)
            assert_eq(label.get(), "test")

        @test(mark="fast")
        async def test_second() -> None:
            assert_eq(await load_fixture(resource()), {2 if scope == "function" else 1})
            assert_eq(label.get(), "test")
        """),
        name=f"test_{scope}_task_affinity",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 2)
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_eq(result["session_teardown_failed"], 0)
    assert_eq(result["run_teardown_failed"], 0)
    if scope == "function":
        assert_eq(
            [case["fixture_teardown_output"] for case in result["tests"]],
            ["closed\n", "closed\n"],
        )
    else:
        assert_eq(result[f"{scope}_teardown_output"], "closed\n")


@test(
    [Param(value=scope, name=scope) for scope in ("function", "session", "run")],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_async_fixture_teardown_warnings_are_not_lost_with_its_setup_context(
    scope: str, arguments: tuple[str, ...]
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import warnings
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test

        @fixture(scope={scope!r})
        async def resource() -> AsyncGenerator[None]:
            yield
            warnings.warn("async teardown warning", stacklevel=1)

        @test(mark="fast")
        async def test_resource() -> None:
            await load_fixture(resource())
        """),
        name=f"test_{scope}_warning_affinity",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    warnings = (
        result["tests"][0]["warnings"]
        if scope == "function"
        else result[f"{scope}_teardown_warnings"]
    )
    assert_eq(len(warnings), 1)
    assert_eq(warnings[0].endswith("UserWarning: async teardown warning"), True)


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_unstarted_function_load_cannot_escape_its_test_cleanup(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        from collections.abc import AsyncGenerator
        from snektest import assert_true, fixture, load_fixture, test

        children: list[asyncio.Task[bool]] = []
        loads: list[asyncio.Future[None]] = []

        @fixture
        async def resource() -> AsyncGenerator[None]:
            children.append(asyncio.create_task(asyncio.Event().wait()))
            await asyncio.Event().wait()
            yield

        @test(mark="fast")
        async def test_abandons() -> None:
            loads.append(asyncio.ensure_future(load_fixture(resource())))

        @test(mark="fast")
        async def test_following() -> None:
            await asyncio.sleep(0)
            assert_true(all(task.done() for task in children + loads))
        """),
        name="test_unstarted_function_fixture",
    )
    result = run_test_subprocess(path, "--timeout", "0.2", *arguments, timeout=10)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["tests"][1]["status"], "passed")
    failures = result["tests"][0].get("fixture_teardown_failures", [])
    assert_eq(
        [entry["exception"]["type"] for entry in failures], ["FixtureTaskLeakError"]
    )
    assert_eq(result["stderr"], "")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_longer_lived_dependency_does_not_share_its_callers_scope_stack(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, fixture, load_fixture, test

        events: list[str] = []

        @fixture(scope="session")
        async def session() -> AsyncGenerator[list[str]]:
            async with anyio.create_task_group() as group:
                group.start_soon(anyio.sleep_forever)
                try:
                    yield events
                finally:
                    assert_eq(events, ["outer closed", "inner closed"])
                    group.cancel_scope.cancel()
                    print("session closed")

        @fixture
        async def inner() -> AsyncGenerator[list[str]]:
            with anyio.CancelScope():
                state = await load_fixture(session())
                yield state
                assert_eq(state, ["outer closed"])
                state.append("inner closed")

        @fixture
        async def outer() -> AsyncGenerator[list[str]]:
            async with anyio.create_task_group() as group:
                group.start_soon(anyio.sleep_forever)
                state = await load_fixture(inner())
                try:
                    yield state
                finally:
                    assert_eq(state, [])
                    state.append("outer closed")
                    group.cancel_scope.cancel()

        @test(mark="fast")
        async def test_resources() -> None:
            assert_eq(await load_fixture(outer()), [])

        @test(mark="fast")
        async def test_function_teardown() -> None:
            assert_eq(await load_fixture(session()), ["outer closed", "inner closed"])
        """),
        name="test_dynamic_scope_dependencies",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 2)
    assert_eq(result["session_teardown_output"], "session closed\n")


@test(mark="slow")
def test_programmatic_host_context_and_tasks_remain_independent() -> None:
    directory = load_fixture(tmp_dir_fixture())
    _ = (directory / "fixture_context_state.py").write_text(
        'from contextvars import ContextVar\nlabel: ContextVar[str] = ContextVar("label", default="unset")\n'
    )
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from collections.abc import AsyncGenerator
        from fixture_context_state import label
        from snektest import assert_eq, fixture, load_fixture, test

        @fixture
        async def resource() -> AsyncGenerator[str]:
            assert_eq(label.get(), "embedding")
            token = label.set("fixture")
            with anyio.CancelScope():
                try:
                    yield label.get()
                finally:
                    assert_eq(label.get(), "fixture")
                    label.reset(token)

        @test(mark="fast")
        async def test_context() -> None:
            assert_eq(await load_fixture(resource()), "fixture")
            assert_eq(label.get(), "embedding")
        """),
        name="test_embedded_fixture_context",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            dedent(f"""
            import asyncio
            import anyio
            from fixture_context_state import label
            from snektest import assert_eq, assert_false
            from snektest.cli import run_tests_programmatic
            from snektest.models import FilterItem

            async def main() -> None:
                label.set("embedding")
                release = asyncio.Event()
                host_task = asyncio.create_task(release.wait())
                try:
                    result = await run_tests_programmatic([FilterItem({str(path)!r})])
                    assert_eq((result.passed, result.exit_code), (1, 0))
                    assert_eq(label.get(), "embedding")
                    assert_false(host_task.done())
                finally:
                    release.set()
                    await host_task

            anyio.run(main, backend="asyncio")
            """),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))


@test(
    [Param(value=scope, name=scope) for scope in ("function", "session")],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_fixture_task_group_failure_is_attributed_without_cancelling_test(
    scope: str, arguments: tuple[str, ...]
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test

        failed = anyio.Event()

        async def service(trigger: anyio.Event) -> None:
            await trigger.wait()
            try:
                raise ValueError("service failed")
            finally:
                failed.set()

        @fixture(scope={scope!r})
        async def resource() -> AsyncGenerator[anyio.Event]:
            trigger = anyio.Event()
            async with anyio.create_task_group() as group:
                group.start_soon(service, trigger)
                yield trigger

        @test(mark="fast")
        async def test_service() -> None:
            trigger = await load_fixture(resource())
            trigger.set()
            await failed.wait()
        """),
        name=f"test_{scope}_background_group_error",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    failures = (
        result["tests"][0]["fixture_teardown_failures"]
        if scope == "function"
        else result["session_teardown_failures"]
    )
    assert_eq(len(failures), 1)
    exception = failures[0]["exception"]
    assert_eq(exception["type"], "ExceptionGroup")
    assert_eq(exception["exceptions"][0]["message"], "service failed")
    assert_eq(result["stderr"], "")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_run_fixture_async_service_remains_live_between_host_commands(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from anyio.abc import SocketAttribute, SocketStream
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, fixture, load_fixture, test

        async def echo(stream: SocketStream) -> None:
            async with stream:
                await stream.send(await stream.receive())

        @fixture(scope="run")
        async def server() -> AsyncGenerator[tuple[str, int]]:
            async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
                address = listener.extra(SocketAttribute.local_address)
                async with anyio.create_task_group() as group:
                    group.start_soon(listener.serve, echo)
                    try:
                        yield address
                    finally:
                        group.cancel_scope.cancel()

        @test(mark="medium")
        async def test_round_trip() -> None:
            host, port = await load_fixture(server())
            async with await anyio.connect_tcp(host, port) as stream:
                await stream.send(b"ping")
                reply = bytearray()
                while len(reply) < 4:
                    reply.extend(await stream.receive())
                assert_eq(bytes(reply), b"ping")
        """),
        name="test_host_async_service",
    )
    result = run_test_subprocess(path, "--timeout", "2", *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["run_teardown_failed"], 0)


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_run_fixture_setup_warning_remains_visible(arguments: tuple[str, ...]) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import warnings
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, fixture, load_fixture, test

        @fixture(scope="run")
        async def resource() -> AsyncGenerator[int]:
            warnings.warn("run setup warning", stacklevel=1)
            yield 42

        @test(mark="fast")
        async def test_resource() -> None:
            assert_eq(await load_fixture(resource()), 42)
        """),
        name="test_host_setup_warning",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    case = result["tests"][0]
    visible = (
        case["captured_output"]
        + "\n".join(case["warnings"])
        + (result["run_teardown_output"] or "")
    )
    assert_eq("UserWarning: run setup warning" in visible, True)


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_abandoned_task_finalizer_cannot_open_a_finished_function_scope(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        from collections.abc import AsyncGenerator
        from snektest import FixtureError, assert_eq, assert_raises, fixture, load_fixture, test

        events: list[str] = []
        tasks: list[asyncio.Task[None]] = []

        @fixture
        async def resource() -> AsyncGenerator[None]:
            events.append("opened too late")
            yield
            events.append("closed")

        async def abandoned(started: asyncio.Event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                with assert_raises(FixtureError, match="teardown has started"):
                    await load_fixture(resource())
                events.append("rejected")

        @test(mark="fast")
        async def test_abandons_task() -> None:
            started = asyncio.Event()
            tasks.append(asyncio.create_task(abandoned(started)))
            await started.wait()

        @test(mark="fast")
        def test_following() -> None:
            assert_eq(events, ["rejected"])
        """),
        name="test_late_function_fixture",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq([case["status"] for case in result["tests"]], ["failed", "passed"])
    assert_eq(result["tests"][0].get("background_failures", []), [])
    assert_eq(result["stderr"], "")


@test(
    [
        Param(value="uncaptured", name="uncaptured"),
        Param(value="opt-out", name="opt-out"),
    ],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_async_fixture_warnings_respect_capture_and_startup_opt_outs(
    mode: str, arguments: tuple[str, ...]
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import warnings
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test

        @fixture(scope="session")
        async def resource() -> AsyncGenerator[None]:
            yield
            warnings.warn("session teardown warning", stacklevel=1)

        @test(mark="fast")
        async def test_resource() -> None:
            await load_fixture(resource())
        """),
        name=f"test_warning_{mode.replace('-', '_')}",
    )
    python_options = ["-X", "context_aware_warnings=0"] if mode == "opt-out" else []
    cli_options = ["-s"] if mode == "uncaptured" else []
    completed = subprocess.run(
        [
            sys.executable,
            *python_options,
            "-m",
            "snektest",
            "--json-output",
            *cli_options,
            *arguments,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    result = json.loads(completed.stdout)
    if mode == "uncaptured":
        assert_eq(result["session_teardown_warnings"], [])
        assert_eq("session teardown warning" in result["uncaptured_output"], True)
    else:
        assert_eq(len(result["session_teardown_warnings"]), 1)
        assert_eq(result["uncaptured_output"], "")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_fixture_raised_cancellation_can_be_caught_before_cached_retry(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, assert_raises, fixture, load_fixture, test

        attempts = 0

        @fixture(scope="session")
        async def resource() -> AsyncGenerator[int]:
            global attempts
            attempts += 1
            with anyio.CancelScope():
                if attempts == 1:
                    raise asyncio.CancelledError
                yield 42
                print("closed")

        @test(mark="fast")
        async def test_retry() -> None:
            with assert_raises(asyncio.CancelledError):
                await load_fixture(resource())
            assert_eq(await load_fixture(resource()), 42)
            assert_eq(attempts, 2)
        """),
        name="test_cancelled_fixture_retry",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["session_teardown_output"], "closed\n")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_body_timeout_during_function_setup_is_not_parent_cancellation(
    arguments: tuple[str, ...],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from collections.abc import AsyncGenerator, Generator
        from snektest import fixture, load_fixture, test

        @fixture
        def dependency() -> Generator[None]:
            yield
            print("dependency closed")

        @fixture
        async def resource() -> AsyncGenerator[None]:
            load_fixture(dependency())
            await anyio.sleep_forever()
            yield

        @test(mark="fast")
        async def test_setup_times_out() -> None:
            await load_fixture(resource())

        @test(mark="fast")
        def test_following() -> None:
            pass
        """),
        name="test_function_setup_timeout",
    )
    result = run_test_subprocess(path, "--timeout", "1", *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq([case["status"] for case in result["tests"]], ["error", "passed"])
    case = result["tests"][0]
    assert_eq(case["exception"]["type"], "TestTimeoutError")
    assert_eq(case["fixture_teardown_output"], "dependency closed\n")
    assert_eq(result["fixture_teardown_failed"], 0)


@test(
    [
        *[
            Param(value=(scope, False), name=scope)
            for scope in ("function", "session", "run")
        ],
        Param(value=("function", True), name="function-fallback"),
    ],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_malformed_async_scope_reports_failure_and_continues_teardown(
    fixture_case: tuple[str, bool], arguments: tuple[str, ...]
) -> None:
    scope, catches_error = fixture_case
    fallback = (
        "except Exception:\n                    yield 44" if catches_error else ""
    )
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import anyio
        from collections.abc import AsyncGenerator
        from snektest import assert_eq, fixture, load_fixture, test

        @fixture(scope={scope!r})
        async def older() -> AsyncGenerator[int]:
            yield 0
            print("older closed")

        @fixture(scope={scope!r})
        async def malformed() -> AsyncGenerator[int]:
            await load_fixture(older())
            async with anyio.create_task_group() as group:
                group.start_soon(anyio.sleep_forever)
                try:
                    yield 42
                    yield 43
                {fallback}
                finally:
                    group.cancel_scope.cancel()

        @test(mark="fast")
        async def test_resource() -> None:
            assert_eq(await load_fixture(malformed()), 42)
        """),
        name=f"test_{scope}_malformed_scope_{catches_error}",
    )
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    failures = (
        result["tests"][0]["fixture_teardown_failures"]
        if scope == "function"
        else result[f"{scope}_teardown_failures"]
    )
    assert_eq(len(failures), 1)
    assert_eq(failures[0]["fixture_name"], "malformed")
    assert_eq("yielded more than once" in json.dumps(failures), True)
    assert_eq("GeneratorExit" in json.dumps(failures), False)
    output = (
        result["tests"][0]["fixture_teardown_output"]
        if scope == "function"
        else result[f"{scope}_teardown_output"]
    )
    assert_eq(output, "older closed\n")
    assert_eq(result["stderr"], "")
