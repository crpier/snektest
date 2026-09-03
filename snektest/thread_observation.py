"""Process-global observation of failures from test-created threads and objects."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Literal, Protocol, cast

from snektest.diagnostics import snapshot_exception
from snektest.models import AssertionFailure, BackgroundFailure


class _UnraisableArgs(Protocol):
    exc_value: BaseException | None
    exc_traceback: TracebackType | None
    err_msg: str | None
    object: object | None


type _UnraisableHook = Callable[[_UnraisableArgs], object]
type _FailureOrigin = Literal["thread", "thread_leak", "unraisable"]


@dataclass
class _Observer:
    baseline_threads: frozenset[threading.Thread]
    failures: list[BackgroundFailure]
    lock: threading.Lock
    previous_thread_hook: Callable[[threading.ExceptHookArgs], object]
    previous_unraisable_hook: _UnraisableHook
    thread_hook: Callable[[threading.ExceptHookArgs], object] | None = None
    unraisable_hook: _UnraisableHook | None = None

    def record(
        self,
        *,
        exception: BaseException,
        traceback: TracebackType | None,
        label: str,
        origin: _FailureOrigin,
        message: str,
    ) -> None:
        diagnostic = snapshot_exception(type(exception), exception, traceback)
        failure = BackgroundFailure(
            exception=replace(diagnostic, message=message),
            label=label,
            origin=origin,
        )
        with self.lock:
            self.failures.append(failure)

    def record_thread_exception(self, args: threading.ExceptHookArgs) -> None:
        exception = args.exc_value
        if exception is None or args.thread in self.baseline_threads:
            _ = self.previous_thread_hook(args)
            return
        thread_name = args.thread.name if args.thread is not None else "unknown"
        self.record(
            exception=exception,
            traceback=args.exc_traceback,
            label=thread_name,
            origin="thread",
            message=(
                f"Thread {thread_name!r} raised {type(exception).__name__}: {exception}"
            ),
        )
        _ = self.previous_thread_hook(args)

    def record_unraisable(self, args: _UnraisableArgs) -> None:
        exception = args.exc_value
        if exception is not None:
            label = args.err_msg or (
                type(args.object).__name__ if args.object is not None else "object"
            )
            self.record(
                exception=exception,
                traceback=args.exc_traceback,
                label=label,
                origin="unraisable",
                message=f"Unraisable exception in {label}: {exception}",
            )
        _ = self.previous_unraisable_hook(args)

    def record_thread_leaks(self) -> None:
        executor_threads = _default_executor_threads()
        leaked_threads = sorted(
            (
                thread
                for thread in threading.enumerate()
                if thread not in self.baseline_threads
                and thread not in executor_threads
                and thread is not threading.current_thread()
                and thread.is_alive()
                and not thread.daemon
            ),
            key=lambda thread: (thread.name, thread.ident or -1),
        )
        for thread in leaked_threads:
            message = f"test leaked non-daemon thread {thread.name!r}"
            try:
                raise AssertionFailure(message)  # noqa: TRY301
            except AssertionFailure as exception:
                self.record(
                    exception=exception,
                    traceback=exception.__traceback__,
                    label=thread.name,
                    origin="thread_leak",
                    message=message,
                )


def _default_executor_threads() -> frozenset[threading.Thread]:
    """Identify persistent workers owned by the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return frozenset[threading.Thread]()
    executor = getattr(loop, "_default_executor", None)
    threads = getattr(executor, "_threads", ())
    return frozenset(
        thread for thread in threads if isinstance(thread, threading.Thread)
    )


@contextmanager
def observe_background_failures() -> Generator[list[BackgroundFailure]]:
    """Observe process-global hooks during one canonically sequential test."""
    previous_thread_hook = threading.excepthook
    previous_unraisable_hook = cast("_UnraisableHook", sys.unraisablehook)
    failures: list[BackgroundFailure] = []
    observer = _Observer(
        baseline_threads=frozenset(threading.enumerate()),
        failures=failures,
        lock=threading.Lock(),
        previous_thread_hook=previous_thread_hook,
        previous_unraisable_hook=previous_unraisable_hook,
    )
    observer.thread_hook = observer.record_thread_exception
    observer.unraisable_hook = observer.record_unraisable
    threading.excepthook = observer.thread_hook
    sys.unraisablehook = cast("Callable[[object], object]", observer.unraisable_hook)
    try:
        yield failures
    finally:
        observer.record_thread_leaks()
        if threading.excepthook is observer.thread_hook:
            threading.excepthook = previous_thread_hook
        if sys.unraisablehook is observer.unraisable_hook:
            sys.unraisablehook = cast(
                "Callable[[object], object]", previous_unraisable_hook
            )
