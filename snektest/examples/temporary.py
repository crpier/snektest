"""Runner-owned temporary directories, usable from sync and async tests.

Default retention is "never": delete after the test and its fixture/task cleanup,
even if it fails. "on-failure" keeps actionable failures, not caught exceptions or
SKIP/XFAIL. "always" keeps successful artifacts too. Completed console/JSON runs
report paths only. Pass base=an_existing_private_path to avoid a small tmpfs or
long socket paths; allocation never mutates TMPDIR or sweeps unrelated data.

Session-fixture setup infers session ownership. Retain-on-failure then considers
any actionable test failure in that execution process and session teardown errors,
not other workers or later run-fixture failures. Each worker/replacement has its
own session; run fixtures and standalone context-manager use are unsupported.

Stop/reap resource consumers before directory cleanup. Recursive removal runs in
a thread and waits through caller cancellation, but threads cannot be interrupted:
filesystem hangs still need an outer timeout. SIGKILL/crashes can leave roots, and
interrupted runs may not emit a completed retained-path report. Retained/orphaned
roots need explicit owner-checked cleanup; no automatic historical pruning occurs.
"""

lazy from collections.abc import Generator
lazy from pathlib import Path

lazy from snektest import assert_eq, fixture, load_fixture, temporary_directory, test


@fixture
def open_report() -> Generator[Path]:
    """Close the resource before the runner considers deleting its directory."""
    root = temporary_directory()
    report = root / "report.txt"
    with report.open("w", encoding="utf-8") as stream:
        _ = stream.write("ready")
        stream.flush()
        yield report
        _ = stream.write(";closed")


@fixture(scope="session")
def session_workspace() -> Generator[Path]:
    """One cached fixture owns this path until its execution process shuts down."""
    root = temporary_directory(retention="on-failure")
    yield root
    # Stop/reap any services using root here, before directory policy runs.


@test(mark="medium")
def test_file_resource() -> None:
    report = load_fixture(open_report())
    assert_eq(report.read_text(encoding="utf-8"), "ready")


@test(mark="medium")
async def test_session_workspace() -> None:
    root = load_fixture(session_workspace())
    _ = (root / "input.txt").write_text("payload", encoding="utf-8")
    assert_eq((root / "input.txt").read_text(encoding="utf-8"), "payload")
