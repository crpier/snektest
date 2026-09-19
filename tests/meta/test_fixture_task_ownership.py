"""Fixture setup failures retain task ownership through reported cleanup."""

import asyncio
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [
        Param(("function", "async"), "async-function"),
        Param(("function", "sync"), "sync-function"),
        Param(("session", "async"), "async-session"),
        Param(("session", "sync"), "sync-session"),
        Param(("run", "async"), "async-run"),
    ],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_failed_setup_retains_task_diagnostics(
    setup_case: tuple[str, str], worker_args: tuple[str, ...]
) -> None:
    """A setup error does not hide the failed fixture's abandoned tasks."""
    directory = load_fixture(tmp_dir_fixture())
    scope, setup = setup_case
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator, Generator
            from snektest import fixture, load_fixture, test

            background_tasks: list[asyncio.Task[object]] = []

            @fixture(scope={scope!r})
            {"async " if setup == "async" else ""}def broken() -> {"AsyncGenerator" if setup == "async" else "Generator"}[None]:
                background_tasks.append(asyncio.create_task(asyncio.Event().wait()))
                raise ValueError("setup failed")
                yield

            @test(mark="fast")
            async def test_setup() -> None:
                _ = {"await " if setup == "async" else ""}load_fixture(broken())
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, *worker_args, timeout=20
    )

    assert_in("setup failed", result["tests"][0]["exception"]["message"])
    failures = (
        result["tests"][0].get("fixture_teardown_failures", [])
        if scope == "function"
        else result[f"{scope}_teardown_failures"]
    )
    assert_eq(len(failures), 1)
    assert_eq(failures[0]["fixture_name"], "broken")
    assert_eq(failures[0]["exception"]["type"], "FixtureTaskLeakError")
    assert_in("1 pending task", failures[0]["exception"]["message"])


@test([Param((), "local"), Param(("--workers", "1"), "worker")], mark="slow")
async def test_pending_session_setup_outlives_cancelled_test_waiter(
    worker_args: tuple[str, ...],
) -> None:
    """Shared setup is fixture-owned even before its first value is ready."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent("""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import assert_eq, assert_raises, fixture, load_fixture, test

            started = asyncio.Event()
            release = asyncio.Event()
            attempts = 0

            @fixture(scope="session")
            async def shared() -> AsyncGenerator[str]:
                global attempts
                attempts += 1
                started.set()
                await release.wait()
                yield "ready"

            @test(mark="fast")
            async def test_cancel_waiter() -> None:
                waiter = asyncio.ensure_future(load_fixture(shared()))
                await started.wait()
                _ = waiter.cancel()
                with assert_raises(asyncio.CancelledError):
                    _ = await waiter

            @test(mark="fast")
            async def test_reuse_pending_setup() -> None:
                release.set()
                result = await load_fixture(shared())
                assert_eq(result, "ready")
                assert_eq(attempts, 1)
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, *worker_args, timeout=20
    )

    assert_eq([entry["status"] for entry in result["tests"]], ["passed", "passed"])
    assert_eq(result["session_teardown_failures"], [])
    assert_eq(result["returncode"], 0)


@test(
    [Param("function", "function"), Param("session", "session"), Param("run", "run")],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_failed_setup_preserves_finalizer_errors(
    scope: str, worker_args: tuple[str, ...]
) -> None:
    """Forced finalization diagnostics accompany, rather than replace, setup errors."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import FixtureError, fixture, load_fixture, test

            background_tasks: list[asyncio.Task[object]] = []

            @fixture(scope={scope!r})
            async def broken() -> AsyncGenerator[None]:
                started = asyncio.Event()

                async def background() -> None:
                    started.set()
                    try:
                        while True:
                            try:
                                await asyncio.Event().wait()
                            except asyncio.CancelledError:
                                continue
                    finally:
                        raise FixtureError("finalizer failed")

                background_tasks.append(asyncio.create_task(background()))
                await started.wait()
                raise ValueError("setup failed")
                yield

            @test(mark="fast")
            async def test_setup() -> None:
                _ = await load_fixture(broken())
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--timeout", "2", *worker_args, timeout=20
    )

    assert_in("setup failed", result["tests"][0]["exception"]["message"])
    failures = (
        result["tests"][0].get("fixture_teardown_failures", [])
        if scope == "function"
        else result[f"{scope}_teardown_failures"]
    )
    assert_eq(
        [(entry["fixture_name"], entry["exception"]["type"]) for entry in failures],
        [("broken", "FixtureTaskLeakError"), ("broken", "FixtureError")],
    )
    assert_eq(failures[1]["exception"]["message"], "finalizer failed")
    assert_eq(result["stderr"], "")


@test(
    [
        Param(("session", ()), "session-local"),
        Param(("session", ("--workers", "1")), "session-worker"),
        Param(("run", ()), "run-local"),
    ],
    [
        Param(None, "clean-finalizer"),
        Param("setup finalizer failed", "broken-finalizer"),
    ],
    mark="slow",
)
async def test_pending_setup_cancellation_is_bounded(
    setup_case: tuple[str, tuple[str, ...]], finalizer_error: str | None
) -> None:
    """A cancelled waiter cannot leave resistant shared setup hanging at shutdown."""
    directory = load_fixture(tmp_dir_fixture())
    scope, worker_args = setup_case
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import FixtureError, assert_raises, fixture, load_fixture, test

            started = asyncio.Event()

            @fixture(scope={scope!r})
            async def shared() -> AsyncGenerator[None]:
                started.set()
                try:
                    while True:
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            continue
                finally:
                    if {finalizer_error!r} is not None:
                        raise FixtureError({finalizer_error!r})
                yield

            @test(mark="fast")
            async def test_cancel_waiter() -> None:
                waiter = asyncio.ensure_future(load_fixture(shared()))
                await started.wait()
                _ = waiter.cancel()
                with assert_raises(asyncio.CancelledError):
                    _ = await waiter
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--timeout", "2", *worker_args, timeout=10
    )

    assert_eq(result["tests"][0]["status"], "passed")
    failures = result[f"{scope}_teardown_failures"]
    assert_eq(
        [(entry["fixture_name"], entry["exception"]["type"]) for entry in failures],
        [("shared", "FixtureTeardownTimeoutError")]
        + ([] if finalizer_error is None else [("shared", "FixtureError")]),
    )
    if finalizer_error is not None:
        assert_in(finalizer_error, failures[1]["exception"]["message"])
    assert_eq(result["returncode"], 1)
    assert_eq(result["stderr"], "")


@test(
    [
        Param(("session", ()), "session-local"),
        Param(("session", ("--workers", "1")), "session-worker"),
        Param(("run", ()), "run-local"),
    ],
    mark="slow",
)
async def test_pending_setup_reports_cancellation_error(
    setup_case: tuple[str, tuple[str, ...]],
) -> None:
    """Shutdown retains errors raised while cooperative setup unwinds."""
    directory = load_fixture(tmp_dir_fixture())
    scope, worker_args = setup_case
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import FixtureError, assert_raises, fixture, load_fixture, test

            started = asyncio.Event()

            @fixture(scope={scope!r})
            async def shared() -> AsyncGenerator[None]:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    raise FixtureError("setup cleanup failed")
                yield

            @test(mark="fast")
            async def test_cancel_waiter() -> None:
                waiter = asyncio.ensure_future(load_fixture(shared()))
                await started.wait()
                _ = waiter.cancel()
                with assert_raises(asyncio.CancelledError):
                    _ = await waiter
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--timeout", "2", *worker_args, timeout=10
    )

    assert_eq(result["tests"][0]["status"], "passed")
    failures = result[f"{scope}_teardown_failures"]
    assert_eq(len(failures), 1)
    assert_eq(failures[0]["fixture_name"], "shared")
    assert_eq(failures[0]["exception"]["type"], "FixtureError")
    assert_in("setup cleanup failed", failures[0]["exception"]["message"])
    assert_eq(result["returncode"], 1)
    assert_eq(result["stderr"], "")


@test(
    [Param("cooperative", "cooperative"), Param("resistant", "resistant")],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_unawaited_function_setup_is_cleaned_before_next_test(
    cancellation: str, worker_args: tuple[str, ...]
) -> None:
    """Ownership cannot hide unfinished function setup when its test returns."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import assert_true, fixture, load_fixture, test

            started = asyncio.Event()
            children: list[asyncio.Task[object]] = []
            setups: list[asyncio.Future[object]] = []

            @fixture
            async def unfinished() -> AsyncGenerator[None]:
                children.append(asyncio.create_task(asyncio.Event().wait()))
                started.set()
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if {cancellation!r} == "cooperative":
                            raise
                yield

            @test(mark="fast")
            async def test_abandon_setup() -> None:
                setups.append(asyncio.ensure_future(load_fixture(unfinished())))
                await started.wait()

            @test(mark="fast")
            def test_following() -> None:
                assert_true(all(task.done() for task in setups + children))
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--timeout", "2", *worker_args, timeout=15
    )

    failures = result["tests"][0].get("fixture_teardown_failures", [])
    assert_eq(
        [entry["exception"]["type"] for entry in failures],
        [
            "FixtureTaskLeakError"
            if cancellation == "cooperative"
            else "FixtureTeardownTimeoutError",
            "FixtureTaskLeakError",
        ],
    )
    assert_eq(
        [entry["fixture_name"] for entry in failures], ["unfinished", "unfinished"]
    )
    assert_eq(result["tests"][1]["status"], "passed")
    assert_eq(result["returncode"], 1)
    assert_eq(result["stderr"], "")
