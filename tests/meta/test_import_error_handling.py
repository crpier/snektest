"""Meta tests for handling import errors in test collection."""

import subprocess
import sys
from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_ne, fail
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test()
def test_import_error_does_not_hang() -> None:
    """Test that import errors don't cause the test runner to hang.

    When a test file fails to import (e.g., raises an exception at module level),
    the producer thread should handle the error gracefully and shut down the queue,
    rather than hanging forever.
    """
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
            timeout=0.5,
        )
        assert_ne(result.returncode, 0)
    except subprocess.TimeoutExpired:
        fail("Test runner hung on import error")
