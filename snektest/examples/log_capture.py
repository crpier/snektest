"""Capture stdlib messages and original structured records explicitly."""

lazy import asyncio
lazy import logging
lazy from collections.abc import AsyncGenerator

lazy from snektest import (
    LogCapture,
    assert_eq,
    capture_logs,
    fixture,
    load_fixture,
    test,
)


@test(mark="fast")
def test_structured_logging() -> None:
    logger = logging.getLogger("example.service")
    # Application handlers stay attached; only necessary level changes are made.
    with capture_logs(logger, level=logging.INFO) as logs:
        logger.info("opened %s", "sqlite", extra={"backend": "sqlite"})
    assert_eq(logs.messages, ("opened sqlite",))
    assert_eq(vars(logs.records[0])["backend"], "sqlite")


@fixture
async def captured_lifecycle() -> AsyncGenerator[LogCapture]:
    logger = logging.getLogger("example.lifecycle")
    with capture_logs(logger) as logs:
        try:
            yield logs
        finally:
            logger.info("fixture teardown")
            assert_eq(logs.messages, ("async body", "fixture teardown"))


@test(mark="fast")
async def test_async_fixture_logging() -> None:
    logs = await load_fixture(captured_lifecycle())
    await asyncio.sleep(0)
    logging.getLogger("example.lifecycle").info("async body")
    assert_eq(logs.messages, ("async body",))
