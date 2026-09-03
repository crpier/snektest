"""Cross-platform subprocess regressions for runner interruption."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from signal import SIGINT
from textwrap import dedent

from snektest import assert_eq, assert_in, assert_true, fail, test


@test(mark="slow")
def test_keyboard_interrupt_runs_fixture_cleanup_before_cli_exit() -> None:
    """The portable interruption path cleans up before returning a usage exit."""
    with tempfile.TemporaryDirectory() as tmp:
        cleanup_file = Path(tmp) / "cleaned"
        test_file = Path(tmp) / "test_interrupted.py"
        _ = test_file.write_text(
            dedent(f"""
                from collections.abc import Generator
                from pathlib import Path

                from snektest import fixture, load_fixture, test

                CLEANUP_FILE = Path({str(cleanup_file)!r})

                @fixture
                def resource() -> Generator[None]:
                    yield None
                    _ = CLEANUP_FILE.write_text("cleaned")

                @test()
                def test_interrupted() -> None:
                    _ = load_fixture(resource())
                    raise KeyboardInterrupt
            """)
        )

        completed = subprocess.run(
            [sys.executable, "-m", "snektest.cli", str(test_file)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert_eq(completed.returncode, 2)
        assert_true(cleanup_file.is_file())
        assert_eq(cleanup_file.read_text(), "cleaned")
        assert_in("Interrupted by user", completed.stdout + completed.stderr)


@test(mark="slow")
def test_posix_sigint_runs_async_fixture_cleanup() -> None:
    """A real terminal interrupt follows the cleanup path on POSIX."""
    if os.name == "nt":
        return

    with tempfile.TemporaryDirectory() as tmp:
        ready_file = Path(tmp) / "ready"
        cleanup_file = Path(tmp) / "cleaned"
        test_file = Path(tmp) / "test_sigint.py"
        _ = test_file.write_text(
            dedent(f"""
                import asyncio
                from collections.abc import AsyncGenerator
                from pathlib import Path

                from snektest import fixture, load_fixture, test

                READY_FILE = Path({str(ready_file)!r})
                CLEANUP_FILE = Path({str(cleanup_file)!r})

                @fixture
                async def resource() -> AsyncGenerator[None]:
                    _ = READY_FILE.write_text("ready")
                    yield None
                    _ = CLEANUP_FILE.write_text("cleaned")

                @test()
                async def test_interrupted() -> None:
                    _ = await load_fixture(resource())
                    await asyncio.Event().wait()
            """)
        )

        process = subprocess.Popen(
            [sys.executable, "-m", "snektest.cli", str(test_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready_deadline = time.monotonic() + 5
            while not ready_file.exists() and time.monotonic() < ready_deadline:
                time.sleep(0.01)
            if not ready_file.exists():
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                fail(f"Test process never became ready: {stdout}{stderr}")

            process.send_signal(SIGINT)
            _ = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                _ = process.communicate()

        assert_eq(process.returncode, 2)
        assert_true(cleanup_file.is_file())
        assert_eq(cleanup_file.read_text(), "cleaned")
