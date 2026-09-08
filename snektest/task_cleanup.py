"""Bounded cancellation for async tasks abandoned by tests or fixtures."""

import asyncio
from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from snektest.diagnostics import snapshot_exception
from snektest.models import DEFAULT_CLEANUP_TIMEOUT_SECONDS, ExceptionDiagnostic

cleanup_budget: ContextVar[float] = ContextVar(
    "snektest_cleanup_budget", default=DEFAULT_CLEANUP_TIMEOUT_SECONDS
)
"""Run cleanup ceiling inherited by async property examples."""
cleanup_failures: ContextVar[list[ExceptionDiagnostic] | None] = ContextVar(
    "snektest_cleanup_failures", default=None
)
"""Attributed cleanup errors discovered inside a test-body adapter."""


@contextmanager
def collect_cleanup(timeout: float | None) -> Generator[list[ExceptionDiagnostic]]:
    """Carry the run budget and retain adapter cleanup diagnostics for its test."""
    failures: list[ExceptionDiagnostic] = []
    budget_token = cleanup_budget.set(
        DEFAULT_CLEANUP_TIMEOUT_SECONDS if timeout is None else timeout
    )
    failures_token = cleanup_failures.set(failures)
    try:
        yield failures
    finally:
        cleanup_failures.reset(failures_token)
        cleanup_budget.reset(budget_token)


@dataclass(frozen=True)
class _CleanupValue[T]:
    value: T


def defer_cancellation[**P, T](
    cleanup: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Finish a bounded cleanup phase before propagating caller cancellation.

    Keep a strong task reference and wait through repeated cancellation requests.
    Cleanup itself remains responsible for its deadlines. Capture BaseException
    inside the child so interruption cannot terminate the event loop prematurely.
    """

    @wraps(cleanup)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        async def capture() -> _CleanupValue[T] | BaseException:
            try:
                return _CleanupValue(await cleanup(*args, **kwargs))
            except BaseException as error:
                return error

        pending = asyncio.create_task(capture(), name="snektest cleanup")
        interruption: asyncio.CancelledError | None = None
        while not pending.done():
            try:
                _ = await asyncio.shield(pending)
            except asyncio.CancelledError as error:
                if interruption is None:
                    interruption = error
        outcome = pending.result()
        if interruption is not None:
            raise interruption
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome.value

    return wrapped


@dataclass(frozen=True)
class TaskCleanup:
    """Counts from one bounded cancellation attempt."""

    resistant: int
    total: int
    failures: tuple[ExceptionDiagnostic, ...] = ()


async def cancel_tasks(
    tasks: set[asyncio.Task[Any]],
    *,
    timeout: float,  # noqa: ASYNC109
) -> TaskCleanup:
    """Cancel tasks, force-closing coroutines that exceed the cleanup ceiling."""
    for task in tasks:
        _ = task.cancel()
    if not tasks:
        return TaskCleanup(resistant=0, total=0)

    completed, resistant = await asyncio.wait(tasks, timeout=timeout)
    resistant_count = len(resistant)
    failures: list[ExceptionDiagnostic] = []
    for task in resistant:
        coroutine = task.get_coro()
        if coroutine is not None:
            try:
                coroutine.close()
            except BaseException as error:
                failures.append(
                    snapshot_exception(type(error), error, error.__traceback__)
                )
        _ = task.cancel()
    if resistant:
        forced_completed, resistant = await asyncio.wait(resistant, timeout=timeout)
        completed.update(forced_completed)
    for task in completed:
        if not task.cancelled():
            _ = task.exception()
    return TaskCleanup(
        resistant=resistant_count, total=len(tasks), failures=tuple(failures)
    )
