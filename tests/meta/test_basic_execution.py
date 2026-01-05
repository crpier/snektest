"""Meta tests for basic test execution using snektest helper functions."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.testing import create_test_file, run_test_subprocess
from tests.fixtures import tmp_dir_fixture


@test()
async def test_simple_passing_test() -> None:
    """Test that simple passing test works."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_example() -> None:
                assert True
        """),
    )

    result = run_test_subprocess(test_file)
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["errors"] == 0
    assert result["returncode"] == 0


@test()
async def test_simple_failing_test() -> None:
    """Test that failures are detected."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_will_fail() -> None:
                assert False
        """),
    )

    result = run_test_subprocess(test_file)
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == 1
    assert result["returncode"] != 0


@test()
async def test_multiple_tests() -> None:
    """Test running multiple tests in one file."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_one() -> None:
                assert True

            @test()
            def test_two() -> None:
                assert True

            @test()
            def test_three() -> None:
                assert True
        """),
    )

    result = run_test_subprocess(test_file)
    assert result["passed"] == 3
    assert result["failed"] == 0
    assert result["errors"] == 0
    assert result["returncode"] == 0
