"""Run-fixture interruption must retain cleanup and command exit semantics."""

import asyncio
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from snektest import Param, assert_eq, test


def _interrupt_setup(
    exception: str, setup: str, arguments: tuple[str, ...]
) -> tuple[int, str, bool, str]:
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        cleaned = directory / "cleaned"
        later = directory / "later"
        ready = directory / "ready"
        interruption = (
            f'_ = Path({str(ready)!r}).write_text("ready"); await asyncio.Event().wait()'
            if exception == "cancel"
            else f"raise {exception}"
        )
        test_file = directory / "test_interrupted.py"
        _ = test_file.write_text(
            dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator, Generator
            from pathlib import Path
            from snektest import fixture, load_fixture, test

            @fixture(scope="run")
            def dependency() -> Generator[None]:
                yield None
                _ = Path({str(cleaned)!r}).write_text("cleaned")

            @fixture(scope="run")
            {"async " if setup == "async" else ""}def resource() -> {"AsyncGenerator" if setup == "async" else "Generator"}[None]:
                _ = load_fixture(dependency())
                {interruption}
                yield None

            @test(mark="fast")
            {"async " if setup == "async" else ""}def test_interrupted() -> None:
                _ = {"await " if setup == "async" else ""}load_fixture(resource())

            @test(mark="medium")
            def test_later() -> None:
                _ = Path({str(later)!r}).write_text("later body ran")
        """)
        )
        command = [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            *arguments,
            str(test_file),
        ]
        if exception == "cancel":
            command = [
                sys.executable,
                "-c",
                dedent(f"""
                import asyncio
                from pathlib import Path
                from snektest import assert_eq, assert_raises
                from snektest.cli import run_tests_programmatic
                from snektest.models import FilterItem

                async def main() -> None:
                    task = asyncio.create_task(run_tests_programmatic(
                        [FilterItem({str(test_file)!r})], timeout=0.2
                    ))
                    while not await asyncio.to_thread(Path({str(ready)!r}).exists):
                        await asyncio.sleep(0.001)
                    _ = task.cancel("original cancellation")
                    with assert_raises(asyncio.CancelledError) as raised:
                        _ = await task
                    assert_eq(str(raised.exception), "original cancellation")

                asyncio.run(main())
            """),
            ]
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=20
        )
        return (
            completed.returncode,
            cleaned.read_text() if cleaned.exists() else "",
            later.exists(),
            completed.stderr,
        )


@test(
    [
        Param(("SystemExit(7)", "sync"), "sync-exit"),
        Param(("SystemExit(7)", "async"), "async-exit"),
        Param(("KeyboardInterrupt", "sync"), "sync-interrupt"),
        Param(("KeyboardInterrupt", "async"), "async-interrupt"),
    ],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_run_setup_interruption_stops_after_cleanup(
    setup_case: tuple[str, str], arguments: tuple[str, ...]
) -> None:
    """Keep the original exit while cleaning dependencies and stopping dispatch."""
    observed = await asyncio.to_thread(_interrupt_setup, *setup_case, arguments)
    assert_eq(
        observed, (7 if setup_case[0] == "SystemExit(7)" else 2, "cleaned", False, "")
    )


@test(mark="slow")
async def test_pending_run_setup_preserves_parent_cancellation() -> None:
    """Cancellation retains its message and cleans established run dependencies."""
    observed = await asyncio.to_thread(_interrupt_setup, "cancel", "async", ())
    assert_eq(observed, (0, "cleaned", False, ""))
