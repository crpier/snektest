"""Meta tests for basic test execution using snektest helper functions."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from tests.fixtures import tmp_dir_fixture
from tests.testutils import create_test_file, run_test_subprocess


@test()
async def test_simple_passing_test() -> None:
    """Test that simple passing test works."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_true

            @test()
            def test_example() -> None:
                assert_true(True)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 0)


@test()
async def test_simple_failing_test() -> None:
    """Test that failures are detected."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, fail

            @test()
            def test_will_fail() -> None:
               fail("This test will fail")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)


@test()
async def test_multiple_tests() -> None:
    """Test running multiple tests in one file."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_true

            @test()
            def test_one() -> None:
                assert_true(True)

            @test()
            def test_two() -> None:
                assert_true(True)

            @test()
            def test_three() -> None:
                assert_true(True)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 3)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 0)
