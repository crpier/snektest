"""Meta tests for default async-hygiene checks."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
async def test_pending_task_fails_async_test() -> None:
    """An async test cannot pass while a spawned task remains pending."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from snektest import test

            @test()
            async def test_leaks_task() -> None:
                _ = asyncio.create_task(asyncio.Event().wait())
                await asyncio.sleep(0)
        """),
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["returncode"], 1)
    assert_eq(result["tests"][0]["exception"]["type"], "AssertionFailure")
    assert_eq(
        result["tests"][0]["exception"]["message"],
        "async test leaked 1 pending task",
    )
