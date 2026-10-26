"""Explicit stdlib log capture through its public interface."""

import asyncio
import logging
from contextlib import ExitStack
from io import StringIO
from threading import Event, Thread
from typing import Any, cast, override

from snektest import (
    AssertionFailure,
    BadRequestError,
    Param,
    assert_eq,
    assert_in,
    assert_is,
    assert_isinstance,
    assert_raises,
    assert_true,
    capture_logs,
    test,
)


@test(mark="fast")
def test_capture_keeps_rendered_messages_and_original_structured_records() -> None:
    logger = logging.getLogger("snektest.capture.basic")
    with capture_logs(logger.name) as logs:
        logger.info("opened %s", "sqlite", extra={"backend": "sqlite"})

    assert_eq(logs.messages, ("opened sqlite",))
    records = assert_isinstance(logs.records, tuple)
    assert_eq(len(records), 1)
    record = assert_isinstance(records[0], logging.LogRecord)
    assert_eq(record.msg, "opened %s")
    assert_eq(record.args, ("sqlite",))
    assert_eq(vars(record)["backend"], "sqlite")


@test(mark="fast")
def test_capture_threshold_does_not_suppress_application_handlers() -> None:
    logger = logging.getLogger("snektest.capture.application")
    previous_level = logger.level
    stream = StringIO()
    application_handler = logging.StreamHandler(stream)
    logger.addHandler(application_handler)
    logger.setLevel(logging.DEBUG)
    try:
        with capture_logs(logger, level=logging.WARNING) as logs:
            logger.debug("application debug")
            logger.warning("captured warning")
        assert_eq(logs.messages, ("captured warning",))
        assert_eq(stream.getvalue(), "application debug\ncaptured warning\n")
        assert_eq(logger.level, logging.DEBUG)
        assert_in(application_handler, logger.handlers)
    finally:
        logger.removeHandler(application_handler)
        application_handler.close()
        logger.setLevel(previous_level)


@test(mark="fast")
def test_capture_is_limited_to_propagating_hierarchy_and_preserves_filters() -> None:
    parent = logging.getLogger("snektest.capture.hierarchy")
    child = logging.getLogger(f"{parent.name}.child")
    unrelated = logging.getLogger("snektest.capture.unrelated")
    with capture_logs(parent) as logs:
        child.info("included child")
        unrelated.error("unrelated")
        old_propagate, old_level, old_disabled = (
            child.propagate,
            child.level,
            child.disabled,
        )
        try:
            child.propagate = False
            child.info("not propagated")
            child.propagate = True
            child.setLevel(logging.ERROR)
            child.warning("disabled by descendant level")
            child.disabled = True
            child.error("disabled descendant")
            child.disabled = False
            child.setLevel(logging.NOTSET)
            child.addFilter(reject_records)
            try:
                child.error("filtered descendant")
            finally:
                child.removeFilter(reject_records)
        finally:
            child.propagate, child.disabled = old_propagate, old_disabled
            child.setLevel(old_level)
    assert_eq(logs.messages, ("included child",))


def reject_records(record: logging.LogRecord) -> bool:
    _ = record
    return False


@test(mark="fast")
async def test_overlapping_captures_can_exit_out_of_order() -> None:
    logger = logging.getLogger("snektest.capture.overlap")
    previous_level = logger.level
    previous_handlers = tuple(logger.handlers)
    logger.setLevel(logging.ERROR)
    outer_entered = asyncio.Event()
    inner_entered = asyncio.Event()
    outer_finished = asyncio.Event()
    outer = capture_logs(logger, level=logging.INFO)
    inner = capture_logs(logger, level=logging.DEBUG)

    async def first() -> None:
        with outer:
            outer_entered.set()
            await inner_entered.wait()
        outer_finished.set()

    async def second() -> None:
        await outer_entered.wait()
        with inner:
            inner_entered.set()
            await outer_finished.wait()
            logger.debug("inner remains active")

    try:
        await asyncio.gather(first(), second())
        assert_eq(inner.messages, ("inner remains active",))
        assert_eq(outer.messages, ())
        assert_eq(logger.level, logging.ERROR)
        assert_eq(tuple(logger.handlers), previous_handlers)
    finally:
        logger.setLevel(previous_level)


@test(mark="fast")
def test_child_capture_keeps_its_threshold_after_parent_capture_exits() -> None:
    parent = logging.getLogger("snektest.capture.parent_exit")
    child = logging.getLogger(f"{parent.name}.child")
    original_parent, original_child = parent.level, child.level
    parent.setLevel(logging.ERROR)
    child.setLevel(logging.NOTSET)
    try:
        with ExitStack() as child_scope:
            with capture_logs(parent) as outer:
                inner = child_scope.enter_context(
                    capture_logs(child, level=logging.INFO)
                )
                child.info("both active")
            child.info("child still active")
        assert_eq(outer.messages, ("both active",))
        assert_eq(inner.messages, ("both active", "child still active"))
        assert_eq(child.level, logging.NOTSET)
        assert_eq(parent.level, logging.ERROR)
    finally:
        parent.setLevel(original_parent)
        child.setLevel(original_child)


@test(mark="fast")
def test_broken_message_rendering_is_safe_and_does_not_discard_records() -> None:
    class BrokenMessage:
        @override
        def __str__(self) -> str:
            msg = "secret rendering failure"
            raise RuntimeError(msg)

    logger = logging.getLogger("snektest.capture.rendering")
    broken = BrokenMessage()
    with capture_logs(logger) as logs:
        logger.info(broken)
        logger.info("missing %s %s", "one")  # noqa: PLE1206 - deliberate broken format
        logger.info("still captured")
    assert_eq(
        logs.messages,
        ("<log message unavailable>", "<log message unavailable>", "still captured"),
    )
    assert_eq(len(logs.records), 3)
    assert_is(logs.records[0].msg, broken)


@test(
    [
        Param(AssertionFailure, name="assertion"),
        Param(RuntimeError, name="exception"),
        Param(asyncio.CancelledError, name="cancellation"),
        Param(KeyboardInterrupt, name="interrupt"),
        Param(SystemExit, name="exit"),
    ],
    mark="fast",
)
def test_capture_restores_state_and_propagates_body_errors(
    error: type[BaseException],
) -> None:
    logger = logging.getLogger(f"snektest.capture.restore.{error.__name__}")
    original_level = logger.level
    handlers, filters = tuple(logger.handlers), tuple(logger.filters)
    disabled, propagate = logger.disabled, logger.propagate
    logs = capture_logs(logger)
    with assert_raises(error), logs:
        logger.info("before error")
        message = "stop"
        raise error(message)
    logger.info("outside capture")
    assert_eq(logs.messages, ("before error",))
    assert_eq(logger.level, original_level)
    assert_eq(tuple(logger.handlers), handlers)
    assert_eq(tuple(logger.filters), filters)
    assert_eq((logger.disabled, logger.propagate), (disabled, propagate))


@test(mark="medium")
def test_capture_includes_threads_and_snapshot_containers_do_not_change() -> None:
    logger = logging.getLogger("snektest.capture.thread")
    with capture_logs(logger) as logs:
        logger.info("before thread")
        earlier_records, earlier_messages = logs.records, logs.messages
        thread = Thread(target=lambda: logger.info("thread record"))
        thread.start()
        thread.join(timeout=5)
        assert_eq(thread.is_alive(), False)
    assert_eq(logs.messages, ("before thread", "thread record"))
    assert_eq(earlier_messages, ("before thread",))
    assert_eq(len(earlier_records), 1)
    assert_eq(logs.records[1].thread, thread.ident)
    logger.warning("after capture")
    assert_eq(logs.messages, ("before thread", "thread record"))


@test(mark="fast")
def test_context_reentry_is_rejected_without_disrupting_the_active_capture() -> None:
    logger = logging.getLogger("snektest.capture.reentry")
    logs = capture_logs(logger)
    with logs:
        with assert_raises(BadRequestError), logs:
            logger.info("unreachable")
        logger.info("outer still active")
    assert_eq(logs.messages, ("outer still active",))
    with assert_raises(BadRequestError), logs:
        logger.info("unreachable reuse")


@test(mark="medium")
def test_record_delivery_already_in_flight_cannot_extend_an_exited_capture() -> None:
    at_delivery, release_delivery = Event(), Event()

    class DelayedRecord(logging.LogRecord):
        @override
        def __getattribute__(self, name: str) -> Any:
            # Pause a real stdlib record after logging has selected its handler.
            if name == "levelno" and not at_delivery.is_set():
                at_delivery.set()
                if not release_delivery.wait(timeout=5):
                    message = "record delivery was not released"
                    raise RuntimeError(message)
            return super().__getattribute__(name)

    logger = logging.getLogger("snektest.capture.late_delivery")
    record = DelayedRecord(logger.name, logging.INFO, __file__, 0, "late", (), None)
    thread = Thread(target=logger.handle, args=(record,))
    try:
        with capture_logs(logger) as logs:
            thread.start()
            assert_true(at_delivery.wait(timeout=5))
    finally:
        release_delivery.set()
        thread.join(timeout=5)
    assert_eq(thread.is_alive(), False)
    assert_eq(logs.messages, ())


@test(
    [
        Param((None, logging.DEBUG), name="implicit-root"),
        Param((object(), logging.DEBUG), name="invalid-logger"),
        Param(("valid", "DEBUG"), name="non-integer-level"),
        Param(("valid", True), name="boolean-level"),
        Param(("valid", -1), name="negative-level"),
    ],
    mark="fast",
)
def test_invalid_capture_configuration_fails_before_entering(
    configuration: tuple[object, object],
) -> None:
    logger, level = configuration
    with assert_raises(BadRequestError):
        _ = capture_logs(cast("Any", logger), level=cast("Any", level))


@test(mark="fast")
def test_nested_captures_keep_independent_thresholds_and_restore_outer_level() -> None:
    logger = logging.getLogger("snektest.capture.nested")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        with capture_logs(logger, level=logging.INFO) as outer:
            with capture_logs(logger, level=logging.DEBUG) as inner:
                logger.debug("inner only")
                logger.info("both")
            assert_eq(logger.level, logging.INFO)
            logger.info("outer only")
        assert_eq(outer.messages, ("both", "outer only"))
        assert_eq(inner.messages, ("inner only", "both"))
        assert_eq(logger.level, logging.ERROR)
    finally:
        logger.setLevel(previous)


@test(mark="fast")
def test_notset_captures_existing_emissions_without_changing_effective_level() -> None:
    logger = logging.getLogger("snektest.capture.notset")
    original = logger.level
    logger.setLevel(logging.INFO)
    try:
        with capture_logs(logger, level=logging.NOTSET) as logs:
            logger.debug("still disabled")
            logger.log(25, "custom level")
            assert_eq(logger.level, logging.INFO)
        assert_eq(logs.messages, ("custom level",))
        assert_eq(logs.records[0].levelno, 25)
    finally:
        logger.setLevel(original)


@test(mark="fast")
def test_root_logger_can_be_selected_explicitly() -> None:
    with capture_logs("") as logs:
        logging.getLogger("capture_root_probe").info("root descendant")
    assert_eq(logs.messages, ("root descendant",))


@test(mark="fast")
def test_capture_respects_disabled_source_logger_and_global_logging_disable() -> None:
    logger = logging.getLogger("snektest.capture.disabled")
    disabled, global_disable = logger.disabled, logger.manager.disable
    try:
        logger.disabled = True
        with capture_logs(logger) as disabled_logs:
            logger.error("disabled source")
        logger.disabled = False
        logging.disable(logging.CRITICAL)
        with capture_logs(logger) as globally_disabled:
            logger.error("disabled globally")
        assert_eq(logger.manager.disable, logging.CRITICAL)
        assert_eq(disabled_logs.messages, ())
        assert_eq(globally_disabled.messages, ())
    finally:
        logger.disabled = disabled
        logging.disable(global_disable)


@test(mark="fast")
def test_message_rendering_does_not_swallow_real_interruptions() -> None:
    class Interrupted:
        @override
        def __str__(self) -> str:
            raise KeyboardInterrupt

    logger = logging.getLogger("snektest.capture.interrupted_render")
    with capture_logs(logger) as logs:
        logger.info(Interrupted())
    with assert_raises(KeyboardInterrupt):
        _ = logs.messages


@test(mark="fast")
def test_records_are_the_original_logrecord_objects() -> None:
    logger = logging.getLogger("snektest.capture.identity")
    record = logging.LogRecord(
        logger.name, logging.INFO, __file__, 0, "raw %s", ("record",), None
    )
    with capture_logs(logger) as logs:
        logger.handle(record)
    assert_is(logs.records[0], record)
    assert_eq(logs.messages, ("raw record",))


@test(mark="fast")
def test_application_handlers_added_during_capture_are_not_removed_on_exit() -> None:
    logger = logging.getLogger("snektest.capture.new_application_handler")
    previous = logger.level
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger.setLevel(logging.INFO)
    try:
        with capture_logs(logger) as logs:
            logger.addHandler(handler)
            logger.info("during")
        logger.info("after")
        assert_eq(stream.getvalue(), "during\nafter\n")
        assert_eq(logs.messages, ("during",))
        assert_in(handler, logger.handlers)
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous)


@test(mark="fast")
def test_failed_entry_rolls_back_capture_owned_state() -> None:
    class RestrictedLogger(logging.Logger):
        @override
        def setLevel(self, level: int | str) -> None:
            if level == logging.DEBUG:
                message = "debug logging is forbidden"
                raise RuntimeError(message)
            super().setLevel(level)

    logger = RestrictedLogger("restricted", level=logging.WARNING)
    logs = capture_logs(logger)
    with assert_raises(RuntimeError), logs:
        logger.info("unreachable")
    assert_eq(logger.handlers, [])
    assert_eq(logger.level, logging.WARNING)
    with capture_logs(logger, level=logging.WARNING) as following:
        logger.warning("following capture works")
    assert_eq(following.messages, ("following capture works",))
