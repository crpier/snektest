"""Tests for the --pdb CLI flag."""

import os
import subprocess
from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq, assert_in
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


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
    ).resolve()

    result = subprocess.run(
        ["uv", "run", "snektest", "--pdb", str(test_file)],
        input=b"p value\nc\n",
        capture_output=True,
        timeout=5,
        check=False,
        env={**os.environ, "RICH_WIDTH": "200"},
    )

    combined_output = result.stdout.decode() + result.stderr.decode()

    normalized_output = os.path.normcase(combined_output).casefold()
    normalized_test_file = os.path.normcase(str(test_file)).casefold()

    assert_eq(result.returncode, 1)
    assert_in(f"{normalized_test_file}::test_failure", normalized_output)
    assert_in(f"> {normalized_test_file}(8)test_failure()", normalized_output)
    assert_in("-> assert_eq(value, 2)", normalized_output)
    assert_in("(pdb) 1", normalized_output)
    assert_in("traceback (most recent call last):", normalized_output)
    assert_in("e       1 != 2", normalized_output)
    assert_in("assertionfailure: 1 != 2", normalized_output)
    assert_in("1 failed, 0 passed", normalized_output)


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
    ).resolve()

    result = subprocess.run(
        ["uv", "run", "snektest", "--pdb", str(test_file)],
        input=b"p value\nc\n",
        capture_output=True,
        timeout=5,
        check=False,
        env={**os.environ, "RICH_WIDTH": "200"},
    )

    combined_output = result.stdout.decode() + result.stderr.decode()

    normalized_output = os.path.normcase(combined_output).casefold()
    normalized_test_file = os.path.normcase(str(test_file)).casefold()

    assert_eq(result.returncode, 1)
    assert_in(f"{normalized_test_file}::test_fix", normalized_output)
    assert_in(f"> {normalized_test_file}(8)fix()", normalized_output)
    assert_in(
        '-> raise runtimeerror("fixture teardown failed")',
        normalized_output,
    )
    assert_in("(pdb) 'fixture value'", normalized_output)
    assert_in("traceback (most recent call last):", normalized_output)
    assert_in("runtimeerror: fixture teardown failed", normalized_output)
    assert_in("fixture teardown failed", normalized_output)
    assert_in("1 fixture teardown failed, 1 passed", normalized_output)
