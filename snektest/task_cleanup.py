"""Bounded cancellation for async tasks abandoned by tests or fixtures."""

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskCleanup:
    """Counts from one bounded cancellation attempt."""

    resistant: int
    total: int


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
    for task in resistant:
        coroutine = task.get_coro()
        if coroutine is not None:
            coroutine.close()
        _ = task.cancel()
    if resistant:
        forced_completed, resistant = await asyncio.wait(resistant, timeout=timeout)
        completed.update(forced_completed)
    for task in completed:
        if not task.cancelled():
            _ = task.exception()
    return TaskCleanup(resistant=resistant_count, total=len(tasks))
