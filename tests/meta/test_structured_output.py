"""Public regressions for versioned machine-readable run output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any, cast
from xml.etree import ElementTree

from snektest import (
    Param,
    __version__,
    assert_eq,
    assert_in,
    assert_is,
    assert_is_not_none,
    assert_true,
    load_fixture,
    test,
)
from snektest.cli import run_tests_programmatic
from snektest.models import FilterItem, RunResult, TestResult
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


class _RecordingReporter:
    """Record the normalized completion passed to a reporting adapter."""

    def __init__(self) -> None:
        self.completed_run: RunResult | None = None
        self.retain_passed_output = True

    def test_finished(self, test_result: TestResult) -> None:
        _ = test_result

    def run_finished(self, run_result: RunResult) -> None:
        self.completed_run = run_result


@test(mark="medium")
async def test_programmatic_runner_returns_reported_normalized_run() -> None:
    """Programmatic callers and reporting adapters receive one run object."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_normalized_run",
    )
    reporter = _RecordingReporter()

    completed_run = await run_tests_programmatic(
        [FilterItem(str(test_file))],
        reporter=reporter,
    )

    assert_is(completed_run, reporter.completed_run)
    assert_eq(completed_run.total_tests, 1)
    assert_eq(completed_run.passed, 1)
    assert_true(completed_run.total_duration >= completed_run.test_results[0].duration)


@test(mark="slow")
def test_json_run_declares_contract_identity_and_duration() -> None:
    """A successful run identifies both schema and framework versions."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 0)
    assert_eq(document["schema_version"], 1)
    assert_eq(document["framework_version"], __version__)
    assert_eq(document["kind"], "test_run")
    assert_eq(document["exit_code"], 0)
    assert_eq(document["total_tests"], 1)
    assert_true(document["total_duration"] >= document["tests"][0]["duration"])


@test(mark="slow")
def test_json_test_entry_retains_execution_metadata() -> None:
    """Each test entry retains output, warnings, duration, and markers."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import warnings

            from snektest import test

            @test(mark="medium")
            def test_reports_metadata() -> None:
                print("captured test output")
                warnings.warn("test warning", stacklevel=1)
        """),
        name="test_entry_metadata",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))
    test_entry = document["tests"][0]

    assert_eq(completed.returncode, 0)
    assert_eq(test_entry["captured_output"], "captured test output\n")
    assert_eq(test_entry["markers"], ["medium"])
    assert_true(test_entry["duration"] >= 0)
    assert_eq(len(test_entry["warnings"]), 1)
    assert_in("UserWarning: test warning", test_entry["warnings"][0])
    assert_in(test_entry["warnings"][0], document["warnings"])


@test(mark="slow")
def test_json_argument_error_uses_versioned_envelope() -> None:
    """Invalid arguments remain parseable and retain usage exit status 2."""
    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", "--unknown"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 2)
    assert_eq(document["schema_version"], 1)
    assert_eq(document["framework_version"], __version__)
    assert_eq(document["kind"], "error")
    assert_eq(document["exit_code"], 2)
    assert_eq(document["error"]["category"], "usage")
    assert_eq(document["error"]["type"], "ParseError")
    assert_eq(document["error"]["message"], "Invalid option: `--unknown`")


@test(mark="slow")
def test_json_filter_error_uses_versioned_envelope() -> None:
    """Invalid test filters retain their argument error type and exit status."""
    missing_test = Path("missing-structured-output-test.py")

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(missing_test)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 2)
    assert_eq(document["kind"], "error")
    assert_eq(document["error"]["category"], "usage")
    assert_eq(document["error"]["type"], "ArgsError")
    assert_in("provided path does not exist", document["error"]["message"])


@test(mark="slow")
def test_json_system_exit_preserves_requested_exit_code() -> None:
    """A test-raised process exit still produces one structured error document."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_exits() -> None:
                raise SystemExit(7)
        """),
        name="test_system_exit",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 7)
    assert_eq(document["kind"], "error")
    assert_eq(document["exit_code"], 7)
    assert_eq(document["error"]["category"], "interrupted")
    assert_eq(document["error"]["type"], "SystemExit")


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "1"), name="workers"),
    ],
    mark="slow",
)
def test_json_collection_error_retains_user_traceback(
    worker_args: tuple[str, ...],
) -> None:
    """Local and worker import failures retain their source diagnostics."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import os
            import warnings

            print("before broken import")
            warnings.warn("broken import warning", stacklevel=1)
            os.write(1, b"raw before broken import\\n")
            raise RuntimeError("broken import")
        """),
        name="test_broken_import",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            *worker_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 2)
    assert_eq(document["kind"], "error")
    assert_eq(document["exit_code"], 2)
    assert_eq(document["error"]["category"], "collection")
    assert_eq(document["error"]["type"], "CollectionError")
    assert_eq(document["error"]["cause"]["type"], "RuntimeError")
    assert_eq(document["collection_output"], "before broken import\n")
    assert_eq(len(document["collection_warnings"]), 1)
    assert_in("raw before broken import", document["uncaptured_output"])
    assert_eq(
        document["error"]["cause"]["traceback"][-1],
        {
            "file": str(test_file.resolve()),
            "function": "<module>",
            "line": 8,
            "source": 'raise RuntimeError("broken import")',
        },
    )


@test(
    [
        Param(value="", name="captured"),
        Param(value="-s", name="capture-disabled"),
    ],
    mark="slow",
)
def test_json_stdout_remains_one_document_with_raw_user_output(
    capture_flag: str,
) -> None:
    """Python, descriptor, and child output cannot prefix the JSON document."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import os
            import subprocess
            import sys

            from snektest import test

            os.write(1, b"import descriptor output\\n")
            os.write(1, bytes([255]))

            @test()
            def test_writes() -> None:
                print("python test output")
                print("python test error", file=sys.stderr)
                os.write(1, b"test descriptor output\\n")
                os.write(2, b"test descriptor error\\n")
                _ = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; print('child output'); print('child error', file=sys.stderr)",
                    ],
                    check=False,
                )
        """),
        name=f"test_raw_output_{capture_flag or 'captured'}",
    )
    capture_args = [capture_flag] if capture_flag else []

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            *capture_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 0)
    assert_eq(document["kind"], "test_run")
    assert_in("import descriptor output", document["uncaptured_output"])
    assert_in("\ufffd", document["uncaptured_output"])
    assert_in("test descriptor output", document["uncaptured_output"])
    assert_in("test descriptor error", document["uncaptured_output"])
    assert_in("child output", document["uncaptured_output"])
    assert_in("child error", document["uncaptured_output"])
    if capture_flag:
        assert_in("python test output", document["uncaptured_output"])
        assert_in("python test error", document["uncaptured_output"])
        assert_eq(document["tests"][0]["captured_output"], "")
    else:
        assert_eq(
            document["tests"][0]["captured_output"],
            "python test output\npython test error\n",
        )


@test(mark="slow")
def test_json_counts_each_function_teardown_failure() -> None:
    """The function teardown count uses failure records rather than test cases."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture
            def first_resource() -> Generator[None]:
                yield
                raise RuntimeError("first teardown")

            @fixture
            def second_resource() -> Generator[None]:
                yield
                raise RuntimeError("second teardown")

            @test()
            def test_uses_both() -> None:
                load_fixture(first_resource())
                load_fixture(second_resource())
        """),
        name="test_function_teardown_count",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 1)
    assert_eq(document["fixture_teardown_failed"], 2)
    assert_eq(len(document["tests"][0]["fixture_teardown_failures"]), 2)


@test(mark="slow")
def test_json_expected_failure_retains_assertion_traceback() -> None:
    """Static expected failures retain the assertion that triggered XFAIL."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_eq, test

            @test(xfail="known defect")
            def test_known_defect() -> None:
                assert_eq(1, 2)
        """),
        name="test_xfail_diagnostic",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))
    exception = document["tests"][0]["exception"]

    assert_eq(completed.returncode, 0)
    assert_eq(exception["type"], "AssertionFailure")
    assert_eq(exception["traceback"][-1]["function"], "test_known_defect")
    assert_eq(exception["traceback"][-1]["source"].strip(), "assert_eq(1, 2)")


@test(mark="slow")
def test_console_counts_each_function_teardown_failure() -> None:
    """The human summary uses the same function teardown unit as JSON."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture
            def first_resource() -> Generator[None]:
                yield
                raise RuntimeError("first teardown")

            @fixture
            def second_resource() -> Generator[None]:
                yield
                raise RuntimeError("second teardown")

            @test()
            def test_uses_both() -> None:
                load_fixture(first_resource())
                load_fixture(second_resource())
        """),
        name="test_console_teardown_count",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 1)
    assert_in("2 fixture teardown failed", completed.stdout)


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "1"), name="workers"),
    ],
    mark="slow",
)
def test_json_retains_collection_output_and_warnings(
    worker_args: tuple[str, ...],
) -> None:
    """Collection diagnostics remain available in local and worker runs."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import warnings

            from snektest import test

            print("import output")
            warnings.warn("import warning", stacklevel=1)

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_collection_diagnostics",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            *worker_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 0)
    assert_eq(document["collection_output"], "import output\n")
    assert_eq(len(document["collection_warnings"]), 1)
    assert_in("UserWarning: import warning", document["collection_warnings"][0])


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "2"), name="workers"),
    ],
    mark="slow",
)
def test_junit_maps_every_test_status(worker_args: tuple[str, ...]) -> None:
    """JUnit classifies every outcome in local and process execution."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_eq, skip, test, xfail

            @test()
            def test_passes() -> None:
                pass

            @test()
            def test_skips() -> None:
                skip("missing service")

            @test()
            def test_expected_failure() -> None:
                xfail("known defect")

            @test(xfail="stale defect")
            def test_unexpected_pass() -> None:
                pass

            @test()
            def test_fails() -> None:
                assert_eq(1, 2)

            @test()
            def test_errors() -> None:
                raise RuntimeError("broken setup")
        """),
        name="test_junit_statuses",
    )
    junit_file = tmp_dir / "results.xml"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--junit-output",
            str(junit_file),
            *worker_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    suite = ElementTree.parse(junit_file).getroot()  # noqa: S314
    cases = {case.attrib["name"]: case for case in suite.findall("testcase")}

    assert_eq(completed.returncode, 1)
    assert_eq(suite.attrib["tests"], "6")
    assert_eq(suite.attrib["failures"], "2")
    assert_eq(suite.attrib["errors"], "1")
    assert_eq(suite.attrib["skipped"], "2")
    assert_eq(list(cases[str(test_file) + "::test_passes"]), [])
    skipped = assert_is_not_none(cases[str(test_file) + "::test_skips"].find("skipped"))
    expected_failure = assert_is_not_none(
        cases[str(test_file) + "::test_expected_failure"].find("skipped")
    )
    unexpected_pass = assert_is_not_none(
        cases[str(test_file) + "::test_unexpected_pass"].find("failure")
    )
    failure = assert_is_not_none(cases[str(test_file) + "::test_fails"].find("failure"))
    error = assert_is_not_none(cases[str(test_file) + "::test_errors"].find("error"))

    assert_eq(skipped.attrib, {"message": "missing service", "type": "skip"})
    assert_eq(
        expected_failure.attrib,
        {"message": "known defect", "type": "xfail"},
    )
    assert_eq(unexpected_pass.attrib["type"], "UnexpectedPass")
    assert_eq(failure.attrib["type"], "AssertionFailure")
    assert_eq(error.attrib["type"], "RuntimeError")


@test(mark="slow")
def test_json_junit_write_error_uses_configuration_envelope() -> None:
    """An unwritable JUnit path cannot replace JSON stdout with human text."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_passes() -> None:
                pass
        """),
        name="test_junit_write_error",
    )
    junit_file = tmp_dir / "missing" / "results.xml"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            "--junit-output",
            str(junit_file),
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    document = cast("dict[str, Any]", json.loads(completed.stdout))

    assert_eq(completed.returncode, 2)
    assert_eq(document["kind"], "error")
    assert_eq(document["error"]["category"], "configuration")
    assert_eq(document["error"]["type"], "BadRequestError")
    assert_in("Could not write JUnit output", document["error"]["message"])


@test(mark="slow")
def test_junit_represents_every_fixture_teardown_failure() -> None:
    """Each function, session, and run teardown failure gets an error case."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture
            def first_resource() -> Generator[None]:
                yield
                raise RuntimeError("first teardown")

            @fixture
            def second_resource() -> Generator[None]:
                yield
                raise RuntimeError("second teardown")

            @fixture(scope="session")
            def session_resource() -> Generator[None]:
                yield
                raise RuntimeError("session teardown")

            @fixture(scope="run")
            def run_resource() -> Generator[None]:
                yield
                raise RuntimeError("run teardown")

            @test()
            def test_uses_resources() -> None:
                load_fixture(first_resource())
                load_fixture(second_resource())
                load_fixture(session_resource())
                load_fixture(run_resource())
        """),
        name="test_junit_teardown",
    )
    junit_file = tmp_dir / "teardown.xml"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--junit-output",
            str(junit_file),
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    suite = ElementTree.parse(junit_file).getroot()  # noqa: S314
    error_cases = {
        case.attrib["name"]: assert_is_not_none(case.find("error")).attrib["type"]
        for case in suite.findall("testcase")
        if case.find("error") is not None
    }

    assert_eq(completed.returncode, 1)
    assert_eq(suite.attrib["tests"], "5")
    assert_eq(suite.attrib["errors"], "4")
    assert_eq(
        error_cases,
        {
            f"{test_file}::test_uses_resources::teardown[second_resource]": (
                "RuntimeError"
            ),
            f"{test_file}::test_uses_resources::teardown[first_resource]": (
                "RuntimeError"
            ),
            "session teardown[session_resource]": "RuntimeError",
            "run teardown[run_resource]": "RuntimeError",
        },
    )
