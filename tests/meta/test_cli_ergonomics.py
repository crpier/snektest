"""Public regressions for daily command-line workflows and project defaults."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    assert_isinstance,
    assert_true,
    load_fixture,
    test,
)
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test(mark="slow")
def test_collect_only_lists_cases_without_executing_bodies() -> None:
    """Collection prints runnable selectors but never calls a test body."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    body_marker = tmp_dir / "collect-body-ran"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from pathlib import Path

            from snektest import Param, test

            @test()
            def test_plain() -> None:
                Path({str(body_marker)!r}).write_text("plain", encoding="utf-8")

            @test([
                Param(value=1, name="one"),
                Param(value=2, name="two"),
            ])
            def test_parameterized(value: int) -> None:
                _ = value
                Path({str(body_marker)!r}).write_text("parameter", encoding="utf-8")
        """),
        name="test_collect_only",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--collect-only", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_eq(
        completed.stdout.splitlines(),
        [
            f"{test_file}::test_plain",
            f"{test_file}::test_parameterized[one]",
            f"{test_file}::test_parameterized[two]",
            "3 tests collected",
        ],
    )
    assert_false(body_marker.exists())
    assert_eq(completed.stderr, "")


@test(mark="slow")
def test_collect_only_json_uses_versioned_collection_document() -> None:
    """JSON collection keeps import diagnostics in one parseable document."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import os
            import warnings

            from snektest import test

            print("collection output")
            warnings.warn("collection warning", stacklevel=1)
            os.write(1, b"raw collection output\\n")

            @test(mark="fast")
            def test_never_runs() -> None:
                raise RuntimeError("body ran")
        """),
        name="test_collect_only_json",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--collect-only",
            "--json-output",
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 0)
    assert_eq(document["schema_version"], 1)
    assert_eq(document["kind"], "collection")
    assert_eq(document["exit_code"], 0)
    assert_eq(document["total_tests"], 1)
    assert_eq(
        document["tests"],
        [{"name": f"{test_file}::test_never_runs", "markers": ["fast"]}],
    )
    assert_eq(document["collection_output"], "collection output\n")
    assert_eq(len(document["collection_warnings"]), 1)
    assert_eq(document["uncaptured_output"], "raw collection output\n")
    assert_eq(completed.stderr, "")


@test(mark="slow")
def test_collect_only_json_error_quarantines_raw_import_output() -> None:
    """A failed structured collection remains one document after raw output."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import os

            os.write(1, b"raw failed collection\\n")
            raise RuntimeError("broken collection")
        """),
        name="test_collect_only_broken",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--collect-only",
            "--json-output",
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 2)
    assert_eq(document["error"]["category"], "collection")
    assert_eq(document["uncaptured_output"], "raw failed collection\n")


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "2"), name="workers"),
    ],
    mark="slow",
)
def test_fail_fast_stops_before_the_next_test_body(
    worker_args: tuple[str, ...],
) -> None:
    """Local and worker fail-fast stop after an actionable result."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    later_body_marker = tmp_dir / "fail-fast-later-body"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from pathlib import Path

            from snektest import assert_eq, test

            @test()
            def test_fails() -> None:
                assert_eq(1, 2)

            @test()
            def test_must_not_run() -> None:
                Path({str(later_body_marker)!r}).write_text("ran", encoding="utf-8")
        """),
        name="test_fail_fast",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--fail-fast",
            *worker_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 1)
    assert_in(f"{test_file}::test_fails", completed.stdout)
    assert_in("Stopped early: 1 of 2 tests ran", completed.stdout)
    assert_false(later_body_marker.exists())
    assert_false("test_must_not_run" in completed.stdout)


@test(mark="slow")
def test_fail_fast_json_distinguishes_selected_and_executed_tests() -> None:
    """Structured fail-fast output reports that part of the plan did not run."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_eq, test

            @test()
            def test_fails() -> None:
                assert_eq(1, 2)

            @test()
            def test_not_run() -> None:
                pass
        """),
        name="test_fail_fast_json",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--fail-fast",
            "--json-output",
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 1)
    assert_eq(document["selected_tests"], 2)
    assert_eq(document["total_tests"], 1)
    assert_eq(document["stopped_early"], True)


@test(mark="slow")
def test_fail_fast_continues_after_skip_and_expected_failure() -> None:
    """Intentional non-failures do not stop a fail-fast run."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    final_body_marker = tmp_dir / "fail-fast-final-body"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from pathlib import Path

            from snektest import assert_eq, skip, test

            @test(xfail="known defect")
            def test_expected_failure() -> None:
                assert_eq(1, 2)

            @test()
            def test_skipped() -> None:
                skip("not available")

            @test()
            def test_passes() -> None:
                Path({str(final_body_marker)!r}).write_text("ran", encoding="utf-8")
        """),
        name="test_fail_fast_outcomes",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "-x", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_true(final_body_marker.exists())
    assert_in("1 passed", completed.stdout)
    assert_in("1 skipped", completed.stdout)
    assert_in("1 expected failure", completed.stdout)


@test(mark="slow")
def test_fail_fast_stops_after_function_fixture_teardown_failure() -> None:
    """A failed function teardown prevents the next test body from starting."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    later_body_marker = tmp_dir / "teardown-fail-fast-later-body"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import Generator
            from pathlib import Path

            from snektest import fixture, load_fixture, test

            @fixture
            def broken_fixture() -> Generator[None]:
                yield None
                raise RuntimeError("broken teardown")

            @test()
            def test_teardown_fails() -> None:
                load_fixture(broken_fixture())

            @test()
            def test_must_not_run() -> None:
                Path({str(later_body_marker)!r}).write_text("ran", encoding="utf-8")
        """),
        name="test_teardown_fail_fast",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "-x", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 1)
    assert_in("1 fixture teardown failed", completed.stdout)
    assert_false(later_body_marker.exists())


@test(mark="slow")
def test_durations_lists_the_slowest_direct_selector() -> None:
    """Duration reporting orders completed tests and prints a reusable selector."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import time

            from snektest import test

            @test()
            def test_slowest() -> None:
                time.sleep(0.05)

            @test()
            def test_faster() -> None:
                time.sleep(0.005)
        """),
        name="test_durations",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--durations",
            "1",
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    _, slowest_output = completed.stdout.split("SLOWEST TESTS", 1)
    assert_in(f"selector: {test_file}::test_slowest", slowest_output)
    assert_false(f"selector: {test_file}::test_faster" in slowest_output)


@test(mark="slow")
def test_project_config_supplies_default_test_paths() -> None:
    """No explicit filter uses test paths from the nearest project config."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-test-paths"
    specs_dir = project_dir / "specs"
    specs_dir.mkdir(parents=True)
    _ = (project_dir / "pyproject.toml").write_text(
        '[tool.snektest]\ntest_paths = ["specs"]\n',
        encoding="utf-8",
    )
    selected_file = create_test_file(
        specs_dir,
        dedent("""
            from snektest import test

            @test()
            def test_selected() -> None:
                pass
        """),
        name="test_selected",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            from snektest import assert_eq, test

            @test()
            def test_decoy() -> None:
                assert_eq(1, 2)
        """),
        name="test_decoy",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_in(f"{Path('specs') / selected_file.name}::test_selected", completed.stdout)
    assert_false("test_decoy" in completed.stdout)


@test(mark="slow")
def test_project_config_supplies_async_timeout() -> None:
    """A configured timeout becomes the test-body default."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-timeout"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        "[tool.snektest]\ntimeout = 0.01\n",
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            import asyncio

            from snektest import test

            @test()
            async def test_too_slow() -> None:
                await asyncio.sleep(0.1)
        """),
        name="test_configured_timeout",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 1)
    assert_in("exceeded the configured timeout of 0.01s", completed.stdout)


@test(mark="slow")
def test_project_config_supplies_marker_selection() -> None:
    """Configured marker selection filters the default test plan."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-marker"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        '[tool.snektest]\nmark = "fast"\n',
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            from snektest import assert_eq, test

            @test(mark="fast")
            def test_fast() -> None:
                pass

            @test(mark="slow")
            def test_slow() -> None:
                assert_eq(1, 2)
        """),
        name="test_configured_marker",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_in("test_fast", completed.stdout)
    assert_false("test_slow" in completed.stdout)


@test(mark="slow")
def test_project_config_selects_json_output() -> None:
    """Configured JSON output uses the same versioned run document as the flag."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-json-output"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        "[tool.snektest]\njson_output = true\n",
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_configured_json",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 0)
    assert_eq(document["kind"], "test_run")
    assert_eq(document["passed"], 1)


@test(mark="slow")
def test_configured_json_output_applies_to_usage_errors() -> None:
    """Project-selected structured output includes argument failures."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-json-error"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        "[tool.snektest]\njson_output = true\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--bogus"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 2)
    assert_eq(document["kind"], "error")
    assert_eq(document["error"]["category"], "usage")


@test(mark="slow")
def test_configured_json_output_applies_to_junit_write_errors() -> None:
    """Post-run configuration failures honor project-selected JSON output."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-json-junit-error"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        dedent("""
            [tool.snektest]
            json_output = true
            junit_output = "missing/results.xml"
        """),
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_configured_json_junit_error",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 2)
    assert_eq(document["kind"], "error")
    assert_eq(document["error"]["category"], "configuration")


@test(mark="slow")
def test_project_config_disables_output_capture() -> None:
    """Configured capture behavior applies when no output flag overrides it."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-capture"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        "[tool.snektest]\ncapture_output = false\n",
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            import sys

            from snektest import test

            @test()
            def test_prints() -> None:
                print("configured uncaptured stdout")
                print("configured uncaptured stderr", file=sys.stderr)
        """),
        name="test_configured_capture",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_in("configured uncaptured stdout", completed.stdout)
    assert_in("configured uncaptured stderr", completed.stderr)


@test(mark="slow")
def test_project_config_supplies_junit_output_path() -> None:
    """A configured JUnit path writes the normalized report relative to the project."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-junit"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        '[tool.snektest]\njunit_output = "results.xml"\n',
        encoding="utf-8",
    )
    _ = create_test_file(
        project_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_configured_junit",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_true((project_dir / "results.xml").is_file())


@test(mark="slow")
def test_cli_flags_override_every_project_default() -> None:
    """Explicit filters and inverse flags take precedence over project defaults."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "configured-overrides"
    selected_dir = project_dir / "selected"
    decoy_dir = project_dir / "decoy"
    selected_dir.mkdir(parents=True)
    decoy_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        dedent("""
            [tool.snektest]
            test_paths = ["decoy"]
            timeout = 0.01
            mark = "slow"
            capture_output = false
            json_output = true
            junit_output = "configured.xml"
        """),
        encoding="utf-8",
    )
    selected_file = create_test_file(
        selected_dir,
        dedent("""
            import asyncio

            from snektest import test

            @test(mark="fast")
            async def test_selected() -> None:
                print("captured configured output")
                await asyncio.sleep(0.03)
        """),
        name="test_selected_override",
    )
    _ = create_test_file(
        decoy_dir,
        dedent("""
            from snektest import assert_eq, test

            @test(mark="slow")
            def test_decoy() -> None:
                assert_eq(1, 2)
        """),
        name="test_decoy_override",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--no-timeout",
            "--no-mark",
            "--capture-output",
            "--no-json-output",
            "--no-junit-output",
            str(selected_file),
        ],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_in("1 passed", completed.stdout)
    assert_false("captured configured output" in completed.stdout)
    assert_false("test_decoy" in completed.stdout)
    assert_false((project_dir / "configured.xml").exists())


@test(mark="slow")
def test_unknown_project_config_key_is_rejected() -> None:
    """A misspelled project default fails instead of being silently ignored."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    project_dir = tmp_dir / "invalid-project-config"
    project_dir.mkdir()
    _ = (project_dir / "pyproject.toml").write_text(
        "[tool.snektest]\ntimeuot = 1\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = assert_isinstance(json.loads(completed.stdout), dict)

    assert_eq(completed.returncode, 2)
    assert_eq(document["error"]["category"], "configuration")
    assert_in("Unknown `tool.snektest` key: timeuot", document["error"]["message"])


@test(mark="slow")
def test_cli_reports_supported_output_schema_versions() -> None:
    """The CLI exposes every structured-output schema accepted by this release."""
    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--output-schema-versions"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 0)
    assert_eq(completed.stdout, "JSON output schema versions: 1\n")
    assert_eq(completed.stderr, "")
