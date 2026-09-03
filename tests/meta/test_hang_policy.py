"""Bounded subprocess checks for work outside Snektest's async timeout."""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from snektest import assert_in, assert_is_none, assert_true, test


def _start_hanging_cli(
    source: str,
) -> tuple[subprocess.Popen[str], Path, tempfile.TemporaryDirectory[str]]:
    """Start generated code whose ready marker distinguishes startup from a hang."""
    temporary_directory = tempfile.TemporaryDirectory()
    directory = Path(temporary_directory.name)
    ready_file = directory / "ready"
    test_file = directory / "test_hang.py"
    _ = test_file.write_text(source.replace("READY_FILE", repr(str(ready_file))))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--timeout",
            "0.05",
            str(test_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, ready_file, temporary_directory


def _wait_until_ready(ready_file: Path) -> None:
    deadline = time.monotonic() + 5
    while not ready_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert_true(ready_file.is_file())


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        _ = process.communicate()


@test(mark="fast")
def test_public_docs_distinguish_timeout_layers() -> None:
    """Users can tell which work each timeout does and does not stop."""
    readme = Path("README.md").read_text()

    for layer in (
        "Async test body",
        "Async cleanup",
        "Collection and imports",
        "Sync and CPU-bound work",
        "Async Hypothesis",
        "Outer command",
    ):
        assert_in(f"| {layer} |", readme)
    assert_in("process-per-case isolation", readme)
    assert_in("15-minute", readme)
    for synchronized_guide in ("AGENTS.md", "snektest/agent_docs.py"):
        assert_in(
            "process-per-case isolation",
            Path(synchronized_guide).read_text(),
        )


@test(mark="fast")
def test_hard_timeout_boundary_decision_is_recorded() -> None:
    """The process-isolation tradeoff stays explicit for future maintainers."""
    decision = Path("docs/adr/0001-hard-timeout-boundary.md").read_text()

    assert_in("process-per-case isolation", decision)
    assert_in("external supervisor", decision)


@test(mark="slow")
def test_local_import_hang_requires_outer_supervisor() -> None:
    """`--timeout` starts after local collection and cannot stop an import."""
    process, ready_file, temporary_directory = _start_hanging_cli(
        """from pathlib import Path
import time

_ = Path(READY_FILE).write_text("ready")
while True:
    time.sleep(1)
"""
    )
    try:
        _wait_until_ready(ready_file)
        time.sleep(0.2)
        assert_is_none(process.poll())
    finally:
        _stop_process(process)
        temporary_directory.cleanup()


@test(mark="slow")
def test_sync_body_hang_requires_outer_supervisor() -> None:
    """`--timeout` cannot interrupt synchronous work in a test body."""
    process, ready_file, temporary_directory = _start_hanging_cli(
        """from pathlib import Path
import time

from snektest import test

@test()
def test_hang() -> None:
    _ = Path(READY_FILE).write_text("ready")
    while True:
        time.sleep(1)
"""
    )
    try:
        _wait_until_ready(ready_file)
        time.sleep(0.2)
        assert_is_none(process.poll())
    finally:
        _stop_process(process)
        temporary_directory.cleanup()
