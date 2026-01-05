"""Meta tests for handling import errors in test collection."""

import subprocess
from textwrap import dedent

from snektest import load_fixture, test
from snektest.testing import create_test_file
from tests.fixtures import tmp_dir_fixture


@test()
async def test_import_error_does_not_hang() -> None:
    """Test that import errors don't cause the test runner to hang.

    When a test file fails to import (e.g., raises an exception at module level),
    the producer thread should handle the error gracefully and shut down the queue,
    rather than hanging forever.
    """
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            # This will raise an exception at import time
            raise RuntimeError("Intentional import error for testing")

            @test()
            def test_unreachable() -> None:
                assert True
        """),
    )

    # This should complete (with an error) rather than hanging
    # The run_test_subprocess has a 0.5 second timeout, so if it hangs
    # it will raise subprocess.TimeoutExpired
    try:
        import sys
        cmd = [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        # We expect a non-zero return code due to the collection error
        assert result.returncode != 0
    except subprocess.TimeoutExpired:
        # If we hit this, the bug is present - the runner is hanging
        raise AssertionError("Test runner hung on import error - bug is present!")
