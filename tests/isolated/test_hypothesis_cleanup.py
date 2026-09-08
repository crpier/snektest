"""Property adapters stop scheduling examples once cancellation begins."""

import asyncio
import threading

from snektest import Param, assert_eq, assert_raises, fail, test
from snektest.decorators import _AsyncExampleState, _run_async_example
from snektest.models import DEFAULT_CLEANUP_TIMEOUT_SECONDS
from snektest.task_cleanup import cleanup_budget, collect_cleanup


@test(mark="medium")
async def test_cancelled_property_rejects_next_example() -> None:
    """A worker asking for another example gets a terminal cancellation outcome."""
    state = _AsyncExampleState(
        active_tasks=set(), handoffs=set(), stopping=threading.Event()
    )
    state.stopping.set()

    async def body() -> None:
        fail("a new example started after cancellation")

    with assert_raises(asyncio.CancelledError):
        await asyncio.to_thread(
            _run_async_example,
            asyncio.get_running_loop(),
            body,
            state=state,
            strategy_values=(),
            param_values=(),
        )
    assert_eq((len(state.active_tasks), len(state.handoffs)), (0, 0))


@test([Param(None, "default"), Param(0.125, "configured")], mark="fast")
def test_adapter_cleanup_budget_tracks_run_timeout(timeout: float | None) -> None:
    """Disabling body limits retains the independent default cleanup ceiling."""
    previous = cleanup_budget.get()
    with collect_cleanup(timeout):
        assert_eq(
            cleanup_budget.get(),
            DEFAULT_CLEANUP_TIMEOUT_SECONDS if timeout is None else timeout,
        )
    assert_eq(cleanup_budget.get(), previous)
