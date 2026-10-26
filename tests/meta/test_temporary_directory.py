"""Managed directory lifetime through real console, JSON, and process runs."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from snektest import (
    Param,
    assert_eq,
    assert_in,
    assert_not_in,
    assert_true,
    load_fixture,
    test,
)
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_owned_directories_are_unique_and_removed_after_success(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-success-{workers}"
    base.mkdir()
    untouched = base / "caller-owned.txt"
    untouched.write_text("keep me")
    path = create_test_file(
        directory,
        dedent(f"""
        from pathlib import Path
        from snektest import assert_ne, assert_true, temporary_directory, test

        @test(mark="medium")
        def writes_owned_files() -> None:
            first = temporary_directory(base=Path({str(base)!r}))
            second = temporary_directory(base=Path({str(base)!r}))
            assert_ne(first, second)
            assert_true(first.is_absolute())
            (first / "data").write_text("owned")
            (second / "data").write_text("owned too")
        """),
        name="test_owned_directory_success",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(sorted(item.name for item in base.iterdir()), ["caller-owned.txt"])
    assert_eq(untouched.read_text(), "keep me")


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_failed_test_can_retain_and_report_owned_evidence(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-retained-{workers}"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from pathlib import Path
        from snektest import fail, temporary_directory, test

        @test(mark="medium")
        def fails_after_writing() -> None:
            root = temporary_directory(base=Path({str(base)!r}), retention="on-failure")
            (root / "evidence.txt").write_text("private evidence must not be attached")
            fail("application failure")
        """),
        name="test_retained_directory_failure",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["failed"], 1)
    retained = result["tests"][0]["retained_directories"]
    assert_eq(result["retained_directories"], retained)
    assert_eq(len(retained), 1)
    root = Path(retained[0]["path"])
    assert_true(root.is_dir())
    assert_eq(root.parent, base)
    assert_eq(retained[0]["retention"], "on-failure")
    assert_eq(retained[0]["reason"], "failure")
    assert_eq(retained[0]["scope"], "function")
    assert_eq(
        (root / "evidence.txt").read_text(), "private evidence must not be attached"
    )


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_console_reports_retained_paths_but_never_reads_their_contents(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-console-{workers}"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from snektest import temporary_directory, test

        @test(mark="medium")
        def preserves_requested_evidence() -> None:
            root = temporary_directory(base={str(base)!r}, retention="always")
            (root / "private.log").write_text("sensitive-content-do-not-print")
        """),
        name="test_console_retained_directory",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    roots = list(base.iterdir())
    assert_eq(len(roots), 1)
    assert_in(str(roots[0]), completed.stdout)
    assert_in("Retained temporary directory", completed.stdout)
    assert_not_in("sensitive-content-do-not-print", completed.stdout + completed.stderr)


@test(
    [Param(None, name="local"), Param(1, name="worker")],
    [Param(False, name="success"), Param(True, name="teardown-failure")],
    mark="slow",
)
def test_session_directory_outlives_cases_and_tracks_session_teardown(
    workers: int | None,
    teardown_failure: bool,  # noqa: FBT001
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-session-{workers}-{teardown_failure}"
    base.mkdir()
    final_statement = 'fail("server shutdown failed")' if teardown_failure else "pass"
    path = create_test_file(
        directory,
        dedent(f"""
        from collections.abc import Generator
        from pathlib import Path
        from snektest import assert_true, fail, fixture, load_fixture, temporary_directory, test

        @fixture(scope="session")
        def server() -> Generator[Path]:
            root = temporary_directory(base={str(base)!r}, retention="on-failure")
            yield root
            assert_true(root.exists())
            (root / "stopped").write_text("server stopped before directory cleanup")
            {final_statement}

        @test(mark="medium")
        def first() -> None:
            root = load_fixture(server())
            (root / "first").write_text("live")

        @test(mark="medium")
        def second() -> None:
            root = load_fixture(server())
            assert_true((root / "first").exists())
        """),
        name=f"test_session_directory_{teardown_failure}",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(
        result["returncode"],
        int(teardown_failure),
        msg=result["stdout"] + result["stderr"],
    )
    assert_eq(result["passed"], 2)
    assert_eq(result["session_teardown_failed"], int(teardown_failure))
    if teardown_failure:
        retained = result["retained_directories"]
        assert_eq(len(retained), 1)
        root = Path(retained[0]["path"])
        assert_eq(retained[0]["scope"], "session")
        assert_true((root / "stopped").exists())
    else:
        assert_eq(list(base.iterdir()), [])


@test(mark="slow")
def test_invalid_policy_is_rejected_without_allocating_a_directory() -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / "base-invalid-policy"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from snektest import BadRequestError, assert_raises, temporary_directory, test

        @test(mark="medium")
        def invalid_policy() -> None:
            with assert_raises(BadRequestError, match="retention"):
                temporary_directory(base={str(base)!r}, retention="misspelled")
        """),
        name="test_invalid_directory_retention",
    )
    result = run_test_subprocess(path)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(list(base.iterdir()), [])


@test(
    [Param(None, name="local"), Param(1, name="worker")],
    [
        Param(("never", "failure", "failed", False), name="cleanup-failure-default"),
        Param(
            ("on-failure", "expected", "passed", False), name="caught-expected-error"
        ),
        Param(("on-failure", "skip", "skipped", False), name="skip"),
        Param(("on-failure", "xfail", "expected_failure", False), name="xfail"),
        Param(("on-failure", "xpass", "unexpected_pass", True), name="xpass"),
        Param(("on-failure", "error", "error", True), name="error"),
        Param(("on-failure", "setup", "error", True), name="setup-error"),
        Param(("on-failure", "teardown", "passed", True), name="teardown-error"),
        Param(("on-failure", "leak", "failed", True), name="test-task-leak"),
        Param(("on-failure", "thread", "error", True), name="thread-error"),
    ],
    mark="slow",
)
def test_retention_uses_final_outcomes_instead_of_consumed_exceptions(
    workers: int | None,
    scenario: tuple[str, str, str, bool],
) -> None:
    retention, outcome, expected_status, keep = scenario
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-outcomes-{workers}-{outcome}"
    base.mkdir()
    body = {
        "failure": 'fail("original failure")',
        "expected": 'with assert_raises(ValueError):\n    raise ValueError("expected server startup failure")',
        "skip": 'skip("not applicable")',
        "xfail": 'xfail("known issue")',
        "xpass": "pass",
        "error": 'raise ValueError("application error")',
        "setup": "pass",
        "teardown": "pass",
        "leak": "_ = asyncio.create_task(asyncio.Event().wait())",
        "thread": "worker = threading.Thread(target=broken_thread)\nworker.start()\nworker.join()",
    }[outcome]
    setup = 'raise ValueError("installation failed")' if outcome == "setup" else "pass"
    teardown = (
        'raise ValueError("shutdown failed")' if outcome == "teardown" else "pass"
    )
    decorator = (
        '@test(mark="medium", xfail="known issue")'
        if outcome == "xpass"
        else '@test(mark="medium")'
    )
    source = dedent(f"""
        import asyncio
        import threading
        from collections.abc import Generator
        from pathlib import Path
        from snektest import assert_raises, assert_true, fail, fixture, load_fixture, skip, temporary_directory, test, xfail

        @fixture
        def resource() -> Generator[Path]:
            root = temporary_directory(base={str(base)!r}, retention={retention!r})
            (root / "created").write_text("live")
            {setup}
            try:
                yield root
            finally:
                assert_true(root.exists())
                {teardown}

        def broken_thread() -> None:
            raise ValueError("unhandled background failure")

        {decorator}
        async def outcome() -> None:
            _ = load_fixture(resource())
        """)
    source += "    " + body.replace("\n", "\n    ") + "\n"
    path = create_test_file(directory, source, name=f"test_outcome_{outcome}")
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(
        result["returncode"],
        int(keep or expected_status == "failed"),
        msg=result["stdout"] + result["stderr"],
    )
    assert_eq(
        result["tests"][0]["status"],
        expected_status,
        msg=result["stdout"] + result["stderr"],
    )
    assert_eq(result["fixture_teardown_failed"], int(outcome == "teardown"))
    assert_eq(len(list(base.iterdir())), int(keep))
    if keep:
        assert_eq(len(result["tests"][0]["retained_directories"]), 1)


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_cleanup_refuses_replaced_roots_and_preserves_the_original_failure(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-replacement-{workers}"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from pathlib import Path
        from snektest import fail, temporary_directory, test

        @test(mark="medium")
        def replaces_owned_root() -> None:
            _ = temporary_directory(base={str(base)!r})
            root = temporary_directory(base={str(base)!r})
            root.rename(Path({str(base)!r}) / "moved-original")
            root.mkdir()
            (root / "caller-owned").write_text("do not delete")
            fail("original application failure")
        """),
        name="test_replaced_directory",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["failed"], 1)
    assert_eq(result["fixture_teardown_failed"], 1)
    test_result = result["tests"][0]
    assert_eq(test_result["exception"]["message"], "original application failure")
    assert_in("Refusing to remove replaced", result["stdout"])
    assert_eq(test_result["retained_directories"][0]["reason"], "cleanup_error")
    replacement = Path(test_result["retained_directories"][0]["path"])
    assert_eq((replacement / "caller-owned").read_text(), "do not delete")
    assert_eq(len(list(base.iterdir())), 2)


@test(mark="slow")
def test_worker_session_retention_does_not_inherit_other_workers_failures() -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / "base-worker-isolation"
    base.mkdir()
    signals = directory / "worker-signals"
    signals.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        from collections.abc import Generator
        from pathlib import Path
        from snektest import fail, fixture, load_fixture, temporary_directory, test

        @fixture(scope="session")
        def server() -> Generator[Path]:
            yield temporary_directory(base={str(base)!r}, retention="on-failure")

        async def meet(peer: str, mine: str) -> Path:
            root = load_fixture(server())
            (root / "owner").write_text(mine)
            signals = Path({str(signals)!r})
            (signals / mine).touch()
            while not (signals / peer).exists():
                await asyncio.sleep(0.01)
            return root

        @test(mark="medium")
        async def failing_worker() -> None:
            _ = await meet("passing", "failing")
            fail("one worker failed")

        @test(mark="medium")
        async def passing_worker() -> None:
            _ = await meet("failing", "passing")
        """),
        name="test_session_worker_isolation",
    )
    result = run_test_subprocess(path, "--workers", "2", timeout=20)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["failed"], 1)
    assert_eq(result["passed"], 1)
    retained = result["retained_directories"]
    assert_eq(len(retained), 1)
    assert_eq((Path(retained[0]["path"]) / "owner").read_text(), "failing")
    assert_eq(len(list(base.iterdir())), 1)


@test(mark="slow")
def test_body_interruption_finishes_resource_teardown_before_directory_policy() -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / "base-interruption"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from collections.abc import AsyncGenerator
        from pathlib import Path
        from snektest import fixture, load_fixture, temporary_directory, test

        @fixture
        async def service() -> AsyncGenerator[Path]:
            root = temporary_directory(base={str(base)!r}, retention="on-failure")
            try:
                yield root
            finally:
                (root / "stopped").write_text("finished resource teardown")

        @test(mark="medium")
        async def interrupted() -> None:
            _ = await load_fixture(service())
            raise SystemExit(37)
        """),
        name="test_directory_interruption",
    )
    result = run_test_subprocess(path)
    assert_eq(result["returncode"], 37, msg=result["stdout"] + result["stderr"])
    roots = list(base.iterdir())
    assert_eq(len(roots), 1)
    assert_eq((roots[0] / "stopped").read_text(), "finished resource teardown")


@test(mark="slow")
def test_recursive_cleanup_allows_an_embedding_event_loop_to_progress() -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / "base-offloop"
    base.mkdir()
    location = directory / "cleanup-location.txt"
    path = create_test_file(
        directory,
        dedent(f"""
        from pathlib import Path
        from snektest import temporary_directory, test

        @test(mark="medium")
        def creates_many_files() -> None:
            root = temporary_directory(base={str(base)!r})
            for index in range(2048):
                (root / str(index)).write_bytes(b"x")
            Path({str(location)!r}).write_text(str(root))
        """),
        name="test_offloop_directory_cleanup",
    )
    launcher = dedent(f"""
        import asyncio
        from pathlib import Path
        from snektest import assert_eq, assert_true
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem

        async def observe() -> bool:
            location = Path({str(location)!r})
            while not location.exists():
                await asyncio.sleep(0)
            root = Path(location.read_text())
            observed_partial_removal = False
            while root.exists():
                try:
                    count = len(list(root.iterdir()))
                except FileNotFoundError:
                    break
                observed_partial_removal |= 0 < count < 2048
                await asyncio.sleep(0)
            return observed_partial_removal

        async def main() -> None:
            observer = asyncio.create_task(observe())
            result = await run_tests_programmatic([FilterItem({str(path)!r})])
            assert_eq(result.exit_code, 0)
            assert_true(await observer)

        asyncio.run(main())
        """)
    completed = subprocess.run(
        [sys.executable, "-c", launcher],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(list(base.iterdir()), [])


@test(
    [Param("function", name="function"), Param("session", name="session")], mark="slow"
)
def test_repeated_cancellation_waits_for_resources_and_applies_directory_policy(
    scope: str,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / f"base-cancel-{scope}"
    base.mkdir()
    started, closing = directory / f"started-{scope}", directory / f"closing-{scope}"
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        from collections.abc import AsyncGenerator
        from pathlib import Path
        from snektest import fixture, load_fixture, temporary_directory, test

        @fixture(scope={scope!r})
        async def service() -> AsyncGenerator[Path]:
            _ = temporary_directory(base={str(base)!r})
            root = temporary_directory(base={str(base)!r}, retention="on-failure")
            try:
                yield root
            finally:
                Path({str(closing)!r}).touch()
                await asyncio.sleep(0.05)
                (root / "stopped").write_text("all resource cleanup finished")

        @test(mark="medium")
        async def waiting() -> None:
            _ = await load_fixture(service())
            Path({str(started)!r}).touch()
            await asyncio.Event().wait()
        """),
        name=f"test_cancel_directory_{scope}",
    )
    launcher = dedent(f"""
        import asyncio
        from pathlib import Path
        from snektest import assert_raises
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem

        async def main() -> None:
            task = asyncio.create_task(run_tests_programmatic([FilterItem({str(path)!r})]))
            while not Path({str(started)!r}).exists():
                await asyncio.sleep(0.001)
            _ = task.cancel()
            while not Path({str(closing)!r}).exists():
                await asyncio.sleep(0.001)
            for _ in range(2):
                _ = task.cancel()
                await asyncio.sleep(0.01)
            with assert_raises(asyncio.CancelledError):
                await task

        asyncio.run(main())
        """)
    completed = subprocess.run(
        [sys.executable, "-c", launcher],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    roots = list(base.iterdir())
    assert_eq(len(roots), 1)
    assert_eq((roots[0] / "stopped").read_text(), "all resource cleanup finished")


@test(mark="slow")
def test_runner_ownership_is_required_and_run_fixture_scope_is_rejected() -> None:
    standalone = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from snektest import FixtureError, assert_raises, temporary_directory; "
                "\nwith assert_raises(FixtureError, match='active snektest run'):\n    temporary_directory()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert_eq(standalone.returncode, 0, msg=standalone.stdout + standalone.stderr)
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import Generator
        from pathlib import Path
        from snektest import FixtureError, assert_raises, fixture, load_fixture, temporary_directory, test

        @fixture(scope="run")
        def unsupported() -> Generator[Path]:
            yield temporary_directory()

        @test(mark="medium")
        def rejects_host_ownership() -> None:
            with assert_raises(FixtureError, match="not run fixtures"):
                _ = load_fixture(unsupported())
        """),
        name="test_rejected_run_directory",
    )
    result = run_test_subprocess(path)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])


@test(mark="slow")
def test_invalid_bytes_base_is_rejected_without_allocating() -> None:
    directory = load_fixture(tmp_dir_fixture())
    base = directory / "base-invalid-bytes"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from snektest import assert_raises, temporary_directory, test

        @test(mark="medium")
        def rejects_bytes_base() -> None:
            with assert_raises(TypeError):
                temporary_directory(base={str(base)!r}.encode())
        """),
        name="test_bytes_directory_base",
    )
    result = run_test_subprocess(path)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(list(base.iterdir()), [])
