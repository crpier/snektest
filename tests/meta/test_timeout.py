"""Meta tests for the global --timeout flag, run end-to-end via subprocess."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
async def test_timeout_reports_hanging_async_test_as_error() -> None:
    """A test that hangs on an await is reported as an error, not left to wedge."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from snektest import test

            @test()
            async def test_hangs() -> None:
                await asyncio.sleep(10)
        """),
    )

    result = run_test_subprocess(test_file, "--timeout", "0.05")

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_eq(result["returncode"], 1)
    assert_eq(result["tests"][0]["exception"]["type"], "TestTimeoutError")


@test()
async def test_timeout_lets_fast_async_test_pass() -> None:
    """A test that finishes within the timeout passes normally."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from snektest import test

            @test()
            async def test_quick() -> None:
                await asyncio.sleep(0)
        """),
    )

    result = run_test_subprocess(test_file, "--timeout", "5")

    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 0)
