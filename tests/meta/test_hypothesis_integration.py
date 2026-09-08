"""Meta tests for running Hypothesis-based tests via snektest."""

import asyncio
from textwrap import dedent

from snektest import Param, assert_in, load_fixture, test
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
async def test_async_hypothesis_timeout_exits_promptly() -> None:
    """A timed-out async example cannot leave its worker thread blocked."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            import asyncio

            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            async def test_prop(_x: int) -> None:
                await asyncio.Event().wait()
            """
        ),
        name="test_hypothesis_async_timeout",
    )

    result = run_test_subprocess(
        test_file,
        "--timeout",
        "0.05",
        timeout=5,
    )
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_eq(result["returncode"], 1)
    assert_eq(result["stderr"], "")
    assert_eq(result["tests"][0]["exception"]["type"], "TestTimeoutError")


@test()
async def test_async_hypothesis_cancellation_exits_promptly() -> None:
    """Cancellation reaches the worker instead of blocking its handoff."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            import asyncio

            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            async def test_prop(_x: int) -> None:
                raise asyncio.CancelledError
            """
        ),
        name="test_hypothesis_async_cancellation",
    )

    result = run_test_subprocess(test_file, timeout=5)
    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)
    assert_eq(result["stderr"], "")
    assert_eq(result["tests"][0]["exception"]["type"], "CancelledError")


@test()
async def test_async_hypothesis_framework_error_exits_promptly() -> None:
    """Catchable framework errors still release the Hypothesis worker."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import BadRequestError, test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0))
            async def test_prop(_x: int) -> None:
                raise BadRequestError("bad property request")
            """
        ),
        name="test_hypothesis_async_framework_error",
    )

    result = run_test_subprocess(test_file, timeout=5)
    assert_eq(result["error"]["type"], "BadRequestError")
    assert_eq(result["error"]["message"], "bad property request")
    assert_eq(result["returncode"], 2)
    assert_eq(result["stderr"], "")


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


@test()
async def test_hypothesis_marked_test_filters_with_cli_mark() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent(
            """
            from hypothesis import Phase, settings
            from hypothesis import strategies as st

            from snektest import assert_eq, test_hypothesis


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0), mark="fast")
            async def test_fast(x: int) -> None:
                assert_eq(x, 0)


            @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
            @test_hypothesis(st.just(0), mark="slow")
            def test_slow(x: int) -> None:
                assert_eq(x, 0)
            """
        ),
        name="test_hypothesis_mark_filter",
    )

    payload = run_test_subprocess(test_file, "--mark", "fast")
    assert_eq(payload["passed"], 1)
    assert_eq(payload["failed"], 0)
    assert_eq(payload["errors"], 0)
    assert_eq(payload["tests"][0]["name"], f"{test_file}::test_fast")
    assert_eq(payload["tests"][0]["markers"], ["fast"])
    assert_eq(payload["returncode"], 0)


@test(
    [Param(mode, mode) for mode in ("once", "forever", "finalizer")],
    [Param((), "local"), Param(("--workers", "1"), "worker")],
    mark="slow",
)
async def test_resistant_hypothesis_example_cleanup_is_bounded(
    mode: str, arguments: tuple[str, ...]
) -> None:
    """Suspended resistant examples cannot leave the executor handoff blocked."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        tmp_dir,
        dedent(f"""
            import asyncio
            from collections.abc import Generator
            from hypothesis import settings
            from hypothesis.strategies import just
            from snektest import FixtureError, fixture, load_fixture, test, test_hypothesis

            @fixture
            def resource() -> Generator[None]:
                yield None
                print("fixture cleaned")

            @settings(max_examples=1, deadline=None, database=None)
            @test_hypothesis(just(1), mark="fast")
            async def test_property(value: int) -> None:
                _ = load_fixture(resource())
                try:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if {mode!r} == "forever":
                            while True:
                                try:
                                    await asyncio.Event().wait()
                                except asyncio.CancelledError:
                                    continue
                        await asyncio.Event().wait()
                finally:
                    if {mode!r} == "finalizer":
                        raise FixtureError("example finalizer failed")

            @test(mark="fast")
            def test_later() -> None:
                pass
        """),
        name="test_resistant_example",
    )

    result = await asyncio.to_thread(
        run_test_subprocess,
        test_file,
        "--timeout",
        "2" if arguments else "0.05",
        *arguments,
        timeout=20,
    )

    assert_eq(result["returncode"], 1)
    assert_eq((result["errors"], result["passed"]), (1, 1))
    assert_eq(result["tests"][0]["exception"]["type"], "TestTimeoutError")
    assert_eq(result["tests"][0]["fixture_teardown_output"], "fixture cleaned\n")
    assert_eq(result["stderr"], "")
    if mode == "finalizer":
        background = result["tests"][0]["background_failures"][0]
        assert_eq(background["origin"], "task_cleanup")
        assert_in("example finalizer failed", background["exception"]["message"])
