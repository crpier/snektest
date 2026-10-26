"""Log capture lifetime through real local and spawned-worker commands."""

import json
from textwrap import dedent

from snektest import Param, assert_eq, assert_not_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [Param(None, name="local"), Param(1, name="worker")],
    [Param("sync", name="sync-fixture"), Param("async", name="async-fixture")],
    mark="slow",
)
def test_capture_spans_async_body_and_fixture_teardown_without_result_attachment(
    workers: int | None,
    fixture_kind: str,
) -> None:
    prefix = "async " if fixture_kind == "async" else ""
    generator = "AsyncGenerator" if fixture_kind == "async" else "Generator"
    await_load = "await " if fixture_kind == "async" else ""
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        import logging
        from collections.abc import {generator}
        from snektest import LogCapture, assert_eq, capture_logs, fixture, load_fixture, test

        logger = logging.getLogger("capture_integration")

        @fixture
        {prefix}def captured() -> {generator}[LogCapture]:
            previous_level, previous_handlers = logger.level, tuple(logger.handlers)
            with capture_logs(logger) as logs:
                yield logs
                logger.info("fixture teardown")
                assert_eq(logs.messages, ("captured only locally", "fixture teardown"))
            assert_eq(logger.level, previous_level)
            assert_eq(tuple(logger.handlers), previous_handlers)

        @test(mark="medium")
        async def body() -> None:
            logs = {await_load}load_fixture(captured())
            await asyncio.to_thread(logger.info, "captured only locally")
            assert_eq(logs.messages, ("captured only locally",))
        """),
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=15)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_not_in("captured only locally", json.dumps(result))


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_async_body_timeout_restores_logging_before_the_next_test(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        import logging
        from snektest import assert_eq, capture_logs, test

        logger = logging.getLogger("capture_timeout")
        original_level, original_handlers = logger.level, tuple(logger.handlers)

        @test(mark="fast")
        async def times_out() -> None:
            with capture_logs(logger):
                await asyncio.sleep(100)

        @test(mark="fast")
        def following() -> None:
            assert_eq(logger.level, original_level)
            assert_eq(tuple(logger.handlers), original_handlers)
            with capture_logs(logger) as logs:
                logger.info("next capture works")
            assert_eq(logs.messages, ("next capture works",))
        """),
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, "--timeout", "1", *arguments, timeout=15)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 1)
    assert_eq(result["passed"], 1)
    assert_eq(result["tests"][0]["exception"]["type"], "TestTimeoutError")
