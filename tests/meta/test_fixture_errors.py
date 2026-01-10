"""Meta tests for fixture error handling."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
def test_function_fixture_teardown_failure() -> None:
    """Test that function fixture teardown failures are reported."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            from snektest import load_fixture, test

            def fixture_with_failing_teardown() -> Generator[None]:
                yield None
                msg = "failing teardown"
                raise ValueError(msg)

            @test()
            def test_with_bad_fixture() -> None:
                _ = load_fixture(fixture_with_failing_teardown())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["fixture_teardown_failed"], 1)


@test()
def test_session_fixture_teardown_failure() -> None:
    """Test that session fixture teardown failures are reported."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            from snektest import load_fixture, session_fixture, test

            @session_fixture()
            def session_fixture_with_failing_teardown() -> Generator[None]:
                yield None
                msg = "failing teardown"
                raise ValueError(msg)

            @test()
            def test_with_bad_session_fixture() -> None:
                _ = load_fixture(session_fixture_with_failing_teardown())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["session_teardown_failed"], 1)


@test()
def test_good_fixture_no_errors() -> None:
    """Test that good fixtures don't report any errors."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            from snektest import load_fixture, test

            def good_fixture() -> Generator[None]:
                yield None

            @test()
            def test_with_good_fixture() -> None:
                _ = load_fixture(good_fixture())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_eq(result["session_teardown_failed"], 0)
