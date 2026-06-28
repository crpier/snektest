"""Tests for the --pdb CLI flag."""

import os
import re
import subprocess
from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq, assert_is_not_none
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file

TRACEBACK_WIDTH = 80


def _pad_traceback_line(line: str) -> str:
    padding = max(0, TRACEBACK_WIDTH - len(line))
    return f"{line}{' ' * padding}"


@test()
def test_pdb_stops_on_failure() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import assert_eq

            @test()
            def test_failure() -> None:
                value = 1
                assert_eq(value, 2)
        """),
    )

    result = subprocess.run(
        ["uv", "run", "snektest", "--pdb", str(test_file)],
        input=b"p value\nc\n",
        capture_output=True,
        timeout=5,
        check=False,
        env={**os.environ, "RICH_WIDTH": "200"},
    )

    combined_output = result.stdout.decode() + result.stderr.decode()

    test_duration_match = re.search(r"FAIL \((\d+\.\d+)s\)", combined_output)
    summary_duration_match = re.search(
        r"1 failed, 0 passed in (\d+\.\d+)s", combined_output
    )
    test_duration_match = assert_is_not_none(test_duration_match)
    summary_duration_match = assert_is_not_none(summary_duration_match)
    test_duration = test_duration_match.group(1)
    summary_duration = summary_duration_match.group(1)

    padded_line = _pad_traceback_line("        assert_eq(value, 2)")
    expected_output = dedent(f"""
        {test_file}::test_failure ... FAIL ({test_duration}s)
        > {test_file}(8)test_failure()
        -> assert_eq(value, 2)
        (Pdb) 1
        (Pdb)\u0020
        =================================== FAILURES ===================================

        {test_file}::test_failure ... FAIL ({test_duration}s)
        Traceback (most recent call last):
          File "{test_file}", line 8, in test_failure
        {padded_line}
        E       1 != 2

        ─────────────────────────────────── SUMMARY ────────────────────────────────────
        FAILED {test_file}::test_failure - AssertionFailure: 1 !…

        ───────────────────────── 1 failed, 0 passed in {summary_duration}s ──────────────────────────
        """).lstrip()

    assert_eq(result.returncode, 1)
    assert_eq(combined_output, expected_output)


@test()
def test_pdb_stops_on_fixture_teardown_failure() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import fixture, load_fixture, test

            @fixture
            def fix():
                value = "fixture value"
                yield value
                raise RuntimeError("fixture teardown failed")

            @test()
            def test_fix() -> None:
                _ = load_fixture(fix())
        """),
    )

    result = subprocess.run(
        ["uv", "run", "snektest", "--pdb", str(test_file)],
        input=b"p value\nc\n",
        capture_output=True,
        timeout=5,
        check=False,
        env={**os.environ, "RICH_WIDTH": "200"},
    )

    combined_output = result.stdout.decode() + result.stderr.decode()

    test_duration_match = re.search(r"OK \((\d+\.\d+)s\)", combined_output)
    summary_duration_match = re.search(
        r"1 fixture teardown failed, 1 passed in (\d+\.\d+)s", combined_output
    )
    test_duration_match = assert_is_not_none(test_duration_match)
    summary_duration_match = assert_is_not_none(summary_duration_match)
    test_duration = test_duration_match.group(1)
    summary_duration = summary_duration_match.group(1)

    padded_line = _pad_traceback_line(
        '        raise RuntimeError("fixture teardown failed")'
    )
    expected_output = dedent(f"""
        {test_file}::test_fix ... OK ({test_duration}s)
        > {test_file}(8)fix()
        -> raise RuntimeError("fixture teardown failed")
        (Pdb) 'fixture value'
        (Pdb)\u0020
        =================================== FAILURES ===================================

        {test_file}::test_fix ... FIXTURE TEARDOWN FAILED: fix
        Traceback (most recent call last):
          File "{test_file}", line 8, in fix
        {padded_line}
        RuntimeError: fixture teardown failed

        ─────────────────────────────────── SUMMARY ────────────────────────────────────
        FIXTURE TEARDOWN FAILED {test_file}::test_fix - fix: Run…

        ───────────────── 1 fixture teardown failed, 1 passed in {summary_duration}s ─────────────────
        """).lstrip()

    assert_eq(result.returncode, 1)
    assert_eq(combined_output, expected_output)
