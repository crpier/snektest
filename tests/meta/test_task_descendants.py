"""Cancellation-created tasks belong to the original cleanup owner."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from typing import Any

from snektest import Param, assert_eq, assert_false, assert_raises, test
from snektest.execution import run_tests
from snektest.models import TestCase, TestName
from snektest.reporting import NullRunReporter
from testutils.helpers import run_test_subprocess


def _run_descendants(owner: str, depth: int, workers: str) -> dict[str, Any]:
    with TemporaryDirectory() as temporary:
        test_file = Path(temporary) / "test_descendants.py"
        _ = test_file.write_text(
            dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import assert_true, fixture, load_fixture, test

            children: list[asyncio.Task[object]] = []

            async def child(depth: int) -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    if depth != 0:
                        children.append(asyncio.create_task(child(depth - 1)))

            @fixture
            async def resource() -> AsyncGenerator[None]:
                children.append(asyncio.create_task(child({depth})))
                await asyncio.sleep(0)
                yield None

            @test(mark="fast")
            async def test_original() -> None:
                if {owner!r} == "fixture":
                    _ = await load_fixture(resource())
                else:
                    children.append(asyncio.create_task(child({depth})))
                    await asyncio.sleep(0)

            @test(mark="fast")
            def test_following() -> None:
                assert_true(len(children) >= 2)
                assert_true(all(task.done() for task in children))
        """)
        )
        arguments = ("--workers", workers) if workers else ()
        return run_test_subprocess(
            test_file, "--timeout", "2" if workers else "0.05", *arguments, timeout=20
        )


@test(
    [Param("test", "test"), Param("fixture", "fixture")],
    [
        Param((depth, workers), f"{depth}-{workers or 'local'}")
        for depth in (1, 4, -1)
        for workers in ("", "1")
    ],
    mark="slow",
)
async def test_cleanup_reaps_owned_descendants(
    owner: str, case: tuple[int, str]
) -> None:
    """Finite and persistently respawning generations finish before the next case."""
    result = await asyncio.to_thread(_run_descendants, owner, case[0], case[1])
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(len(result["tests"]), 2)
    assert_eq(result["tests"][1]["status"], "passed")
    if owner == "fixture":
        assert_eq(len(result["tests"][0]["fixture_teardown_failures"]), 1)
    else:
        assert_eq(result["tests"][0]["status"], "failed")
    assert_eq(result["stderr"], "")


@test(mark="fast")
async def test_descendant_cleanup_preserves_embedding_task() -> None:
    """Re-scanning one owner must not cancel an unrelated application's task."""
    embedding_task = asyncio.create_task(asyncio.Event().wait())
    children: list[asyncio.Task[object]] = []

    async def parent() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            children.append(asyncio.create_task(asyncio.Event().wait()))

    async def body() -> None:
        children.append(asyncio.create_task(parent()))
        await asyncio.sleep(0)

    try:
        _ = await run_tests(
            [
                TestCase(
                    function=body,
                    markers=(),
                    name=TestName(
                        file_path=Path(__file__), func_name="body", params_part=""
                    ),
                )
            ],
            reporter=NullRunReporter(),
            timeout=0.1,
        )
        assert_eq([task.done() for task in children], [True, True])
        assert_false(embedding_task.done())
    finally:
        _ = embedding_task.cancel()
        with assert_raises(asyncio.CancelledError):
            await embedding_task
