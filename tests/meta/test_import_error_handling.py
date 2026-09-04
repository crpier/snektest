"""Meta tests for handling import errors in test collection."""

import json
import subprocess
import sys
from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq, assert_in, assert_ne, assert_not_in, fail
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test()
def test_missing_explicit_test_name_exits_unsuccessfully() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_one() -> None:
                pass
        """),
    )

    result = subprocess.run(
        [sys.executable, "-m", "snektest.cli", f"{test_file}::aaa"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(result.returncode, 2)
    assert_in("No test named `aaa`", result.stdout)
    assert_not_in("0 passed", result.stdout)


@test()
def test_import_error_does_not_hang() -> None:
    """An import error aborts collection without starting execution or hanging."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_true

            raise RuntimeError("Intentional import error for testing")

            @test()
            def test_unreachable() -> None:
                assert_true(True)
        """),
    )

    try:
        cmd = [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)]
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        payload = json.loads(result.stdout)
        assert_ne(result.returncode, 0)
        assert_eq(payload["kind"], "error")
        assert_eq(payload["error"]["category"], "collection")
        assert_eq(payload["error"]["cause"]["type"], "RuntimeError")
        assert_in(
            'raise RuntimeError("Intentional import error for testing")',
            payload["error"]["cause"]["traceback"][-1]["source"],
        )
        assert_not_in("0 passed", result.stdout)
    except subprocess.TimeoutExpired:
        fail("Test runner hung on import error")
