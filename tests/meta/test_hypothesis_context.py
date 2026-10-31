"""Async properties retain Hypothesis controls without polluting host tasks."""

lazy import json
lazy import subprocess
lazy import sys
lazy from textwrap import dedent
lazy from typing import Literal

lazy from snektest import (
    Param,
    assert_eq,
    assert_in,
    temporary_directory,
    test,
)
lazy from testutils.helpers import create_test_file

_BACKENDS: list[Param[Literal["asyncio", "trio"]]] = [
    Param(value="asyncio", name="asyncio"),
    Param(value="trio", name="trio"),
]
_WORKERS = [Param(value=None, name="local"), Param(value=2, name="workers")]


@test(_BACKENDS, _WORKERS, mark="slow")
def test_async_controls_and_shrunk_failure_notes(
    backend: Literal["asyncio", "trio"], workers: int | None
) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from hypothesis import assume, currently_in_test_context, event, note, settings, strategies as st, target
        from snektest import assert_eq, assert_lt, assert_true, test_hypothesis

        @settings(max_examples=8, deadline=None, database=None, derandomize=True)
        @test_hypothesis(st.data(), mark="medium")
        async def test_controls(data: st.DataObject) -> None:
            assert_true(currently_in_test_context())
            assert_eq(settings.default.max_examples, 8)
            value = data.draw(st.integers(min_value=0, max_value=10), label="before")
            assume(value >= 0)
            event("before suspension")
            target(float(value), label="before")
            await anyio.sleep(0)
            assert_true(currently_in_test_context())
            extra = data.draw(st.integers(min_value=0, max_value=10), label="after")
            event("after suspension")
            target(float(extra), label="after")
            note(f"passing values: {value}, {extra}")

        @settings(max_examples=8, deadline=None, database=None, derandomize=True)
        @test_hypothesis(st.data(), mark="medium")
        async def test_shrink(data: st.DataObject) -> None:
            await anyio.sleep(0)
            value = data.draw(st.integers(min_value=7, max_value=30), label="failure-value")
            note(f"async-shrunk: {value}")
            assert_lt(value, 7)
    """),
    )
    command = [sys.executable, "-m", "snektest", "--backend", backend, "--json-output"]
    if workers is not None:
        command.extend(("--workers", str(workers)))
    completed = subprocess.run(
        [*command, str(path)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    data = json.loads(completed.stdout)
    assert_eq(
        (data["passed"], data["failed"], data["errors"]),
        (1, 1, 0),
        msg=completed.stdout,
    )
    failure = data["tests"][1]["exception"]
    assert_in("async-shrunk: 7", "\n".join(failure["notes"]))
    assert_in("failure-value", "\n".join(failure["notes"]))
    assert_eq(data["tests"][0]["captured_output"], "")


@test(_BACKENDS, mark="slow")
def test_hypothesis_context_is_absent_from_unrelated_embedding_tasks(
    backend: Literal["asyncio", "trio"],
) -> None:
    directory = temporary_directory()
    _ = (directory / "harness_state.py").write_text(
        "import anyio\nentered = anyio.Event()\nobserved = anyio.Event()\n"
    )
    path = create_test_file(
        directory,
        dedent("""
        from hypothesis import currently_in_test_context, note, settings, strategies as st
        from snektest import assert_true, test_hypothesis
        from harness_state import entered, observed

        @settings(max_examples=2, deadline=None, database=None)
        @test_hypothesis(st.just(1), mark="medium")
        async def test_example(value: int) -> None:
            assert_true(currently_in_test_context())
            entered.set()
            await observed.wait()
            note("after unrelated host ran")
            assert_true(currently_in_test_context())

    """),
    )
    script = dedent(f"""
        import anyio
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem
        from snektest import assert_eq, assert_false
        from hypothesis import currently_in_test_context
        from hypothesis.reporting import report, with_reporter
        from harness_state import entered, observed
        messages: list[str] = []
        async def main() -> None:
            async def watch() -> None:
                await entered.wait()
                assert_false(currently_in_test_context())
                report("host-observed")
                observed.set()
            async with anyio.create_task_group() as group:
                group.start_soon(watch)
                result = await run_tests_programmatic([FilterItem({str(path)!r})], backend={backend!r})
                assert_eq(result.passed, 1)
            assert_false(currently_in_test_context())
        with with_reporter(messages.append):
            anyio.run(main, backend={backend!r})
        assert_eq(messages, ["host-observed"])
    """)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(completed.stdout, "")


@test(_BACKENDS, mark="slow")
def test_cancelled_examples_keep_context_through_finalizers_and_restore_it(
    backend: Literal["asyncio", "trio"],
) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from pathlib import Path
        from hypothesis import currently_in_test_context, note, settings, strategies as st
        from hypothesis.control import cleanup
        from snektest import assert_eq, assert_false, assert_true, test, test_hypothesis

        @settings(max_examples=2, deadline=None, database=None)
        @test_hypothesis(st.data(), mark="medium")
        async def test_timeout(data: st.DataObject) -> None:
            try:
                await anyio.sleep_forever()
            finally:
                assert_true(currently_in_test_context())
                note("cancelled before shield")
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(0.05)
                    assert_true(currently_in_test_context())
                    note("cancelled after shield")
                    assert_eq(data.draw(st.just(42)), 42)
                    cleanup(lambda: Path("worker-cleanup").touch())
                Path("finalized").touch()

        @test(mark="fast")
        def test_following() -> None:
            assert_false(currently_in_test_context())
    """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--backend",
            backend,
            "--timeout",
            "0.5",
            "--json-output",
            str(path),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    data = json.loads(completed.stdout)
    assert_eq(
        (data["passed"], data["errors"], data["failed"]),
        (1, 1, 0),
        msg=completed.stdout,
    )
    assert_eq(data["tests"][0]["exception"]["type"], "TestTimeoutError")
    assert_eq(data["fixture_teardown_failed"], 0)
    assert_eq((directory / "finalized").exists(), True)
    assert_eq((directory / "worker-cleanup").exists(), True)


@test(_BACKENDS, mark="slow")
def test_context_bridge_preserves_native_tasks_scopes_and_thrown_exceptions(
    backend: Literal["asyncio", "trio"],
) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        from contextvars import ContextVar
        import anyio
        from hypothesis import currently_in_test_context, note, settings, strategies as st
        from hypothesis.control import cleanup
        from hypothesis.reporting import report, with_reporter
        from snektest import assert_eq, assert_false, assert_true, fixture, load_fixture, test_hypothesis
        context: ContextVar[int] = ContextVar("body", default=0)
        started: list[int] = []
        cleaned: list[int] = []

        @fixture
        async def resource() -> AsyncGenerator[int]:
            assert_false(currently_in_test_context())
            with anyio.CancelScope():
                yield 42
                await anyio.sleep(0)
                assert_false(currently_in_test_context())
                assert_eq(cleaned, started)

        def raises() -> None:
            raise ValueError("native handoff")

        @settings(max_examples=2, deadline=None, database=None)
        @test_hypothesis(st.just(1), mark="medium")
        async def test_native(value: int) -> None:
            token = context.set(23)
            try:
                assert_eq(await load_fixture(resource()), 42)
                started.append(value)
                cleanup(lambda: cleaned.append(value))
                with anyio.CancelScope():
                    messages: list[str] = []
                    with with_reporter(messages.append):
                        report("before suspension")
                        await anyio.sleep(0)
                        report("after suspension")
                    assert_eq(messages, ["before suspension", "after suspension"])
                    assert_eq(await anyio.to_thread.run_sync(lambda: 17), 17)
                    try:
                        await anyio.to_thread.run_sync(raises)
                    except ValueError as error:
                        assert_eq(str(error), "native handoff")
                        note("handled thrown exception")
                    assert_true(currently_in_test_context())
                    assert_eq(context.get(), 23)
            finally:
                context.reset(token)
    """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--backend",
            backend,
            "--json-output",
            str(path),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(json.loads(completed.stdout)["passed"], 1)
