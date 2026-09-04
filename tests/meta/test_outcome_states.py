"""Public regressions for skip, expected-failure, and unexpected-pass states."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

from snektest import assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


def _run_json(test_file: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            *arguments,
            str(test_file),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


@test(mark="slow")
def test_dynamic_skip_reports_reason_without_failing() -> None:
    """A skipped test has its own JSON state, count, and successful exit."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import skip, test

            @test()
            def test_requires_database() -> None:
                skip("requires a local database")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["skipped"], 1)
    assert_eq(summary["passed"], 0)
    assert_eq(summary["tests"][0]["status"], "skipped")
    assert_eq(summary["tests"][0]["reason"], "requires a local database")


@test(mark="slow")
def test_dynamic_expected_failure_reports_reason_without_failing() -> None:
    """An explicit expected failure has its own JSON state and successful exit."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, xfail

            @test()
            def test_known_defect() -> None:
                xfail("tracked parser defect")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["expected_failures"], 1)
    assert_eq(summary["passed"], 0)
    assert_eq(summary["tests"][0]["status"], "expected_failure")
    assert_eq(summary["tests"][0]["reason"], "tracked parser defect")


@test(mark="slow")
def test_static_expected_failure_classifies_assertion() -> None:
    """A decorated known assertion failure is expected rather than failed."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_eq, test

            @test(xfail="tracked arithmetic defect")
            def test_known_defect() -> None:
                assert_eq(1 + 1, 3)
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["expected_failures"], 1)
    assert_eq(summary["failed"], 0)
    assert_eq(summary["tests"][0]["status"], "expected_failure")
    assert_eq(summary["tests"][0]["reason"], "tracked arithmetic defect")


@test(mark="slow")
def test_unexpected_pass_is_strict() -> None:
    """Passing a statically expected failure is XPASS and fails the command."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test(xfail="tracked arithmetic defect")
            def test_fixed_without_removing_xfail() -> None:
                pass
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["unexpected_passes"], 1)
    assert_eq(summary["passed"], 0)
    assert_eq(summary["tests"][0]["status"], "unexpected_pass")
    assert_eq(summary["tests"][0]["reason"], "tracked arithmetic defect")


@test(mark="slow")
def test_blank_dynamic_reason_is_an_error() -> None:
    """A dynamic outcome cannot silently publish an empty explanation."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import skip, test

            @test()
            def test_missing_reason() -> None:
                skip("  ")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["errors"], 1)
    assert_eq(summary["tests"][0]["status"], "error")
    assert_eq(
        summary["tests"][0]["exception"]["message"],
        "Outcome reason must be a non-empty, already-trimmed string",
    )


@test(mark="slow")
def test_async_skip_tears_down_established_fixture() -> None:
    """A dynamic async skip cannot bypass an established function fixture."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    teardown_record = tmp_dir / "teardown-record"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import AsyncGenerator
            from pathlib import Path

            from snektest import fixture, load_fixture, skip, test

            @fixture
            async def resource() -> AsyncGenerator[str]:
                try:
                    yield "ready"
                finally:
                    Path({str(teardown_record)!r}).write_text("torn down")

            @test()
            async def test_requires_service() -> None:
                _ = await load_fixture(resource())
                skip("service is unavailable")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["tests"][0]["status"], "skipped")
    assert_eq(teardown_record.read_text(), "torn down")


@test(mark="slow")
def test_console_reports_every_outcome_reason() -> None:
    """Human output names each non-pass state and keeps its explanation."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_eq, skip, test, xfail

            @test()
            def test_skipped() -> None:
                skip("database is unavailable")

            @test()
            def test_expected_failure() -> None:
                xfail("tokenizer defect")

            @test(xfail="resolved parser bug")
            def test_unexpected_pass() -> None:
                assert_eq(1, 1)
        """),
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", str(test_file)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(completed.stdout.count("database is unavailable"), 2)
    assert_eq(completed.stdout.count("tokenizer defect"), 2)
    assert_eq(completed.stdout.count("resolved parser bug"), 2)
    assert_eq(completed.stdout.count("SKIP"), 2)
    assert_eq(completed.stdout.count("XFAIL"), 2)
    assert_eq(completed.stdout.count("XPASS"), 2)
    assert_in(
        "1 unexpected pass, 1 skipped, 1 expected failure, 0 passed in",
        completed.stdout,
    )


@test(mark="slow")
def test_skipped_body_keeps_each_fixture_teardown_failure() -> None:
    """Teardown failures remain counted independently from a dynamic outcome."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator

            from snektest import fixture, load_fixture, skip, test

            @fixture
            def first_resource() -> Generator[str]:
                yield "first"
                raise RuntimeError("first teardown")

            @fixture
            def second_resource() -> Generator[str]:
                yield "second"
                raise RuntimeError("second teardown")

            @test()
            def test_unavailable_after_setup() -> None:
                _ = load_fixture(first_resource())
                _ = load_fixture(second_resource())
                skip("dependency unavailable")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["skipped"], 1)
    assert_eq(summary["fixture_teardown_failed"], 2)
    assert_eq(len(summary["tests"][0]["fixture_teardown_failures"]), 2)

    console_completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", str(test_file)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert_eq(console_completed.returncode, 1)
    assert_in("2 fixture teardown failed", console_completed.stdout)


@test(mark="slow")
def test_parameter_outcomes_survive_marker_filtering_in_workers() -> None:
    """Selected parameter cases keep XFAIL semantics in process workers."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import Param, assert_eq, test

            @test(
                [
                    Param(value=1, name="broken"),
                    Param(value=2, name="fixed"),
                ],
                mark="fast",
                xfail="known comparison defect",
            )
            def test_comparison(actual: int) -> None:
                assert_eq(actual, 2)

            @test(mark="slow")
            def test_not_selected() -> None:
                pass
        """),
    )

    completed = _run_json(test_file, "--mark", "fast", "--workers", "2")

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["expected_failures"], 1)
    assert_eq(summary["unexpected_passes"], 1)
    assert_eq(summary["passed"], 0)
    assert_eq(len(summary["tests"]), 2)
    assert_eq(
        [entry["status"] for entry in summary["tests"]],
        ["expected_failure", "unexpected_pass"],
    )


@test(mark="slow")
def test_static_expected_failure_does_not_hide_error() -> None:
    """An xfail declaration covers assertions, not unexpected exceptions."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test(xfail="known assertion defect")
            def test_broken_setup() -> None:
                raise RuntimeError("database driver crashed")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["expected_failures"], 0)
    assert_eq(summary["errors"], 1)
    assert_eq(summary["tests"][0]["status"], "error")
    assert_eq(summary["tests"][0]["exception"]["type"], "RuntimeError")


@test(mark="slow")
def test_background_error_overrides_dynamic_skip() -> None:
    """An intentional outcome cannot hide an observed background exception."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from threading import Thread

            from snektest import skip, test

            @test()
            def test_background_failure() -> None:
                def crash() -> None:
                    raise RuntimeError("worker thread crashed")

                thread = Thread(target=crash)
                thread.start()
                thread.join()
                skip("optional environment unavailable")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["skipped"], 0)
    assert_eq(summary["errors"], 1)
    assert_eq(summary["tests"][0]["status"], "error")
    assert_eq(summary["tests"][0]["background_failures"][0]["origin"], "thread")


@test(mark="slow")
def test_task_leak_overrides_dynamic_expected_failure() -> None:
    """An expected outcome cannot hide an abandoned async task."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio

            from snektest import test, xfail

            @test()
            async def test_abandons_task() -> None:
                _ = asyncio.create_task(asyncio.Event().wait())
                await asyncio.sleep(0)
                xfail("known response defect")
        """),
    )

    completed = _run_json(test_file)

    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    summary = cast("dict[str, Any]", json.loads(completed.stdout))
    assert_eq(summary["expected_failures"], 0)
    assert_eq(summary["failed"], 1)
    assert_eq(summary["tests"][0]["status"], "failed")
    assert_eq(
        summary["tests"][0]["exception"]["message"],
        "async test leaked 1 pending task",
    )
