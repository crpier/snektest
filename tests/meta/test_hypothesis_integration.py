"""Meta tests for running Hypothesis-based tests via snektest."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
async def test_hypothesis_sync_test_passes() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import assert_eq, test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            def test_prop(x: int) -> None:
                assert_eq(x, 0)
            """
        ),
        name="test_hypothesis_sync_pass",
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 0)


@test()
async def test_hypothesis_async_test_passes() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import assert_eq, test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            async def test_prop(x: int) -> None:
                assert_eq(x, 0)
            """
        ),
        name="test_hypothesis_async_pass",
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 0)


@test()
async def test_hypothesis_failure_counts_as_failed() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import assert_gt, test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            def test_prop(x: int) -> None:
                assert_gt(x, 0)
            """
        ),
        name="test_hypothesis_fail",
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)
