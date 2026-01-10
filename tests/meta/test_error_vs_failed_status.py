"""Meta tests for verifying ERROR vs FAILED status distinction."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from tests.fixtures import tmp_dir_fixture
from tests.testutils import create_test_file, run_test_subprocess


@test()
async def test_assertion_failure_shows_as_failed() -> None:
    """Test that assertion failures (using assert_eq) show as FAILED, not ERROR."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import assert_eq

            @test()
            def test_will_fail_assertion() -> None:
                assert_eq(1, 2)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)


@test()
async def test_exception_shows_as_error() -> None:
    """Test that exceptions (like ValueError) show as ERROR, not FAILED."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_will_error() -> None:
                raise ValueError("Something went wrong")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_eq(result["returncode"], 1)


@test()
async def test_bare_assert_shows_as_error() -> None:
    """Test that bare assert statements show as ERROR (not FAILED).

    This encourages users to use the assertion functions instead.
    """
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_eq

            @test()
            def test_with_bare_assert() -> None:
                assert_eq(1, 2, "Numbers don't match")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_eq(result["returncode"], 1)


@test()
async def test_mixed_failures_and_errors() -> None:
    """Test that failed and error counts are tracked separately."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import assert_eq, assert_true

            @test()
            def test_assertion_failure_1() -> None:
                assert_eq(1, 2)

            @test()
            def test_assertion_failure_2() -> None:
                assert_true(False)

            @test()
            def test_error_1() -> None:
                raise ValueError("Error 1")

            @test()
            def test_error_2() -> None:
                raise TypeError("Error 2")

            @test()
            def test_error_3() -> None:
                1 / 0  # ZeroDivisionError

            @test()
            def test_passing() -> None:
                assert_eq(1, 1)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 2)
    assert_eq(result["errors"], 3)
    assert_eq(result["returncode"], 1)


@test()
async def test_all_assertion_types_show_as_failed() -> None:
    """Test that all assertion helper functions result in FAILED status."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test
            from snektest.assertions import (
                assert_eq,
                assert_ne,
                assert_true,
                assert_false,
                assert_in,
                assert_is,
                assert_is_none,
            )

            @test()
            def test_assert_eq_fail() -> None:
                assert_eq(1, 2)

            @test()
            def test_assert_ne_fail() -> None:
                assert_ne(1, 1)

            @test()
            def test_assert_true_fail() -> None:
                assert_true(False)

            @test()
            def test_assert_false_fail() -> None:
                assert_false(True)

            @test()
            def test_assert_in_fail() -> None:
                assert_in(5, [1, 2, 3])

            @test()
            def test_assert_is_fail() -> None:
                a = [1, 2]
                b = [1, 2]
                assert_is(a, b)

            @test()
            def test_assert_is_none_fail() -> None:
                assert_is_none(42)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 7)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)


@test()
async def test_various_exception_types_show_as_error() -> None:
    """Test that different exception types all show as ERROR."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_value_error() -> None:
                raise ValueError("Value error")

            @test()
            def test_type_error() -> None:
                raise TypeError("Type error")

            @test()
            def test_key_error() -> None:
                d = {}
                _ = d["missing_key"]

            @test()
            def test_index_error() -> None:
                lst = []
                _ = lst[0]

            @test()
            def test_zero_division() -> None:
                1 / 0
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 5)
    assert_eq(result["returncode"], 1)
