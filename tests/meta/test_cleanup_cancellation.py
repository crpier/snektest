"""Supervised regressions for cancellation arriving inside cleanup."""

import asyncio
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from snektest import Param, assert_eq, test


def _run_cancelled_cleanup(
    scope: str, requests: int
) -> subprocess.CompletedProcess[str]:
    with TemporaryDirectory() as temporary:
        script = Path(temporary) / "probe.py"
        _ = script.write_text(
            dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator, Generator
            from pathlib import Path
            from snektest import assert_eq, assert_raises, fixture, load_fixture, test
            from snektest.execution import run_tests
            from snektest.models import TestCase, TestName
            from snektest.reporting import NullRunReporter

            events: list[str] = []
            children: list[asyncio.Task[None]] = []
            started: asyncio.Event

            @fixture(scope="run")
            def root() -> Generator[None]:
                yield None
                events.append("run cleaned")

            @fixture(scope={("function" if scope == "tasks" else scope)!r})
            def older() -> Generator[None]:
                yield None
                events.append("older cleaned")

            @fixture(scope={("function" if scope == "tasks" else scope)!r})
            async def newer() -> AsyncGenerator[None]:
                yield None
                if {scope!r} == "tasks":
                    events.append("newer unwound")
                    return
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("newer unwound")

            async def child() -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    try:
                        if {scope!r} == "tasks":
                            started.set()
                            await asyncio.Event().wait()
                    finally:
                        events.append("task cleaned")

            @test(mark="fast")
            async def body() -> None:
                _ = load_fixture(root())
                _ = load_fixture(older())
                _ = await load_fixture(newer())
                children.append(asyncio.create_task(child()))
                await asyncio.sleep(0)

            async def main() -> None:
                global started
                started = asyncio.Event()
                task = asyncio.create_task(run_tests(
                    [TestCase(function=body, markers=(), name=TestName(
                        file_path=Path(__file__), func_name="body", params_part=""
                    ))], reporter=NullRunReporter(), timeout=0.05
                ))
                await started.wait()
                for request in range({requests}):
                    _ = task.cancel(str(request))
                    await asyncio.sleep(0)
                with assert_raises(asyncio.CancelledError):
                    _ = await task
                expected = ["newer unwound", "older cleaned", "task cleaned", "run cleaned"]
                if {scope!r} not in ("function", "tasks"):
                    expected = ["task cleaned", "newer unwound", "older cleaned", "run cleaned"]
                assert_eq(events, expected)

            asyncio.run(main())
        """)
        )
        return subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )


@test(
    [
        Param("function", "function"),
        Param("session", "session"),
        Param("run", "run"),
        Param("tasks", "tasks"),
    ],
    [Param(1, "once"), Param(2, "repeated")],
    mark="slow",
)
async def test_cancellation_during_cleanup(scope: str, requests: int) -> None:
    """All established fixtures and owned tasks finish before cancellation escapes."""
    completed = await asyncio.to_thread(_run_cancelled_cleanup, scope, requests)
    assert_eq((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))
