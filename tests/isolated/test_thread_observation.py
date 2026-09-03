"""Direct execution checks for process-global observation hook ownership."""

from __future__ import annotations

import gc
import sys
import threading
from pathlib import Path

from snektest import assert_eq, assert_is, assert_isinstance, test
from snektest.execution import execute_test
from snektest.models import ErrorResult, TestCase, TestName


@test(mark="medium")
async def test_existing_hooks_are_chained_and_restored() -> None:
    """Observation preserves hooks installed by an embedding application."""
    previous_thread_hook = threading.excepthook
    previous_unraisable_hook = sys.unraisablehook
    calls: list[str] = []

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        calls.append(f"thread:{args.thread.name if args.thread else 'unknown'}")

    def unraisable_hook(_args: object) -> None:
        calls.append("unraisable")

    def body() -> None:
        def break_in_thread() -> None:
            msg = "thread boom"
            raise RuntimeError(msg)

        class BrokenFinalizer:
            def __del__(self) -> None:
                msg = "finalizer boom"
                raise RuntimeError(msg)

        worker = threading.Thread(name="custom-hook-worker", target=break_in_thread)
        worker.start()
        worker.join()
        value = BrokenFinalizer()
        del value
        _ = gc.collect()

    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook
    try:
        result = await execute_test(
            TestCase(
                function=body,
                markers=("medium",),
                name=TestName(
                    file_path=Path(__file__),
                    func_name="test_nested_hook_observation",
                    params_part="",
                ),
            )
        )
        assert_is(threading.excepthook, thread_hook)
        assert_is(sys.unraisablehook, unraisable_hook)
    finally:
        threading.excepthook = previous_thread_hook
        sys.unraisablehook = previous_unraisable_hook

    _ = assert_isinstance(result.result, ErrorResult)
    assert_eq(calls, ["thread:custom-hook-worker", "unraisable"])
    assert_eq(len(result.background_failures), 2)
    assert_eq(result.background_failures[0].origin, "thread")
    assert_eq(result.background_failures[1].origin, "unraisable")
