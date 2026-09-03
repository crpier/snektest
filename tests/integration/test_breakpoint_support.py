"""Test that breakpoint() works without -s flag."""

import os
import re
import subprocess
from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq, assert_in
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


def _normalize_durations(output: str) -> str:
    return re.sub(r"\d+\.\d+s", "<duration>", output)


@test()
def test_breakpoint_works_without_s_flag() -> None:
    """Test that breakpoint() allows pdb interaction without -s flag.

    This test verifies that when a test contains breakpoint(), the stdin
    proxy detects the read and automatically disables output capture,
    allowing pdb to function properly.
    """
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import assert_eq

            @test()
            def test_with_breakpoint() -> None:
                x = 1
                breakpoint()
                y = 2
                assert_eq(x + y, 3)
        """),
    ).resolve()

    result = subprocess.run(
        ["uv", "run", "snektest", str(test_file)],
        input=b"pp locals()\nc\n",
        capture_output=True,
        timeout=5,
        check=False,
    )

    stdout = result.stdout.decode()
    stderr = result.stderr.decode()
    combined_output = stdout + stderr

    normalized_output = os.path.normcase(
        _normalize_durations(combined_output)
    ).casefold()

    assert_eq(result.returncode, 0)
    assert_in(
        os.path.normcase(f"> {test_file}(8)test_with_breakpoint()").casefold(),
        normalized_output,
    )
    assert_in("-> breakpoint()", normalized_output)
    assert_in("(pdb) {'x': 1}", normalized_output)
    assert_in(
        os.path.normcase(f"{test_file}::test_with_breakpoint").casefold(),
        normalized_output,
    )
    assert_in("ok (<duration>)", normalized_output)
    assert_in("1 passed in <duration>", normalized_output)


@test()
def test_breakpoint_can_inspect_variables() -> None:
    """Test that we can inspect variables when breakpoint is hit.

    This verifies that pdb is actually working and we can see variable values.
    """
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import assert_eq

            @test()
            def test_with_breakpoint() -> None:
                x = 1
                breakpoint()
                y = 2
                assert_eq(x + y, 3)
        """),
    ).resolve()

    pdb_commands = b"pp x\nc\n"

    result = subprocess.run(
        ["uv", "run", "snektest", str(test_file)],
        input=pdb_commands,
        capture_output=True,
        timeout=5,
        check=False,
    )

    stdout = result.stdout.decode()
    stderr = result.stderr.decode()
    combined_output = stdout + stderr

    normalized_output = os.path.normcase(
        _normalize_durations(combined_output)
    ).casefold()

    assert_eq(result.returncode, 0)
    assert_in(
        os.path.normcase(f"> {test_file}(8)test_with_breakpoint()").casefold(),
        normalized_output,
    )
    assert_in("-> breakpoint()", normalized_output)
    assert_in("(pdb) 1", normalized_output)
    assert_in(
        os.path.normcase(f"{test_file}::test_with_breakpoint").casefold(),
        normalized_output,
    )
    assert_in("ok (<duration>)", normalized_output)
    assert_in("1 passed in <duration>", normalized_output)
