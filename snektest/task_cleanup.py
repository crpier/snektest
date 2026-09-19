"""Task ownership and bounded cleanup shared by tests and fixtures."""

import asyncio
from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from snektest.diagnostics import snapshot_exception
from snektest.models import (
    DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    ExceptionDiagnostic,
    RunInfrastructureError,
)

_task_owner: ContextVar[object | None] = ContextVar("snektest_task_owner", default=None)
"""The innermost test or fixture owns newly created tasks, not its caller."""


@contextmanager
def task_scope(owner: object) -> Generator[None]:
    """Attribute child tasks to one lifetime, restoring the parent on exit.

    Descendants inherit ownership through their task context. Fixture setup and
    teardown use the same identity even if setup raises before yielding.
    """
    token = _task_owner.set(owner)
    try:
        yield
    finally:
        _task_owner.reset(token)


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


def _force_close_tasks(tasks: set[asyncio.Task[Any]]) -> list[ExceptionDiagnostic]:
    """Contain synchronous finalizer errors and always wake the closed task."""
    failures: list[ExceptionDiagnostic] = []
    for task in tasks:
        coroutine = task.get_coro()
        if coroutine is not None:
            try:
                task.get_context().run(coroutine.close)
            except BaseException as error:
                failures.append(
                    snapshot_exception(type(error), error, error.__traceback__)
                )
        _ = task.cancel()
    return failures


async def cancel_tasks(  # noqa: C901
    tasks: set[asyncio.Task[Any]],
    *,
    timeout: float,  # noqa: ASYNC109
    discover: Callable[[], set[asyncio.Task[Any]]] | None = None,
) -> TaskCleanup:
    """Reap owned generations under one deadline, then force-close survivors.

    Re-scan after each cancellation wave. At the deadline, close newly spawned
    coroutines before giving them a chance to start another generation. A run
    cannot safely continue if tasks still survive that final bounded attempt.
    Synchronous finalizers remain subject to the outer process timeout.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    pending = {task for task in tasks if not task.done()}
    total = 0
    resistant_count = 0
    failures: list[ExceptionDiagnostic] = []
    while pending:
        total += len(pending)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0:
            for task in pending:
                _ = task.cancel()
            completed, pending = await asyncio.wait(pending, timeout=remaining)
            for task in completed:
                if not task.cancelled():
                    _ = task.exception()
        if pending:
            resistant_count += len(pending)
            failures.extend(_force_close_tasks(pending))
            newcomers = discover() - pending if discover is not None else set()
            total += len(newcomers)
            failures.extend(_force_close_tasks(newcomers))
            completed, survivors = await asyncio.wait(pending | newcomers, timeout=0)
            for task in completed:
                if not task.cancelled():
                    _ = task.exception()
            if discover is not None:
                survivors |= discover()
            if survivors:
                msg = (
                    f"Task cleanup could not stop {len(survivors)} owned tasks "
                    f"within {timeout:g}s; refusing to continue the run."
                )
                raise RunInfrastructureError(msg)
            break
        pending = discover() if discover is not None else set()
    return TaskCleanup(resistant=resistant_count, total=total, failures=tuple(failures))


@defer_cancellation
async def cancel_owned_tasks(
    owner: object,
    *,
    timeout: float,  # noqa: ASYNC109
) -> TaskCleanup:
    """Reap one owner's descendants without touching nested or unrelated owners.

    Discovery and cancellation share a deadline, including cancellation-created
    descendants. Repeated caller cancellation waits for this cleanup to finish.
    """

    def owned_tasks() -> set[asyncio.Task[Any]]:
        current_task = asyncio.current_task()
        return {
            task
            for task in asyncio.all_tasks()
            if task is not current_task
            and not task.done()
            and task.get_context().get(_task_owner) is owner
        }

    return await cancel_tasks(owned_tasks(), timeout=timeout, discover=owned_tasks)
