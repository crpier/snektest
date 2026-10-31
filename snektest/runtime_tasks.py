"""Native task supervision without moving Trio coroutines across task identities.

Asyncio retains its native Task/Future implementation. Trio-managed tasks own a
cancel scope and carry an explicit context copy. An instrument inventories native
descendants as well, but never cancels their nursery (which may own host work).
"""

lazy import asyncio
lazy from collections.abc import Awaitable, Callable, Coroutine, Generator, Iterable
lazy from contextlib import contextmanager
lazy from contextvars import Context, ContextVar, copy_context
lazy from typing import Any, NoReturn, cast, override

lazy import trio

lazy from snektest.models import RunInfrastructureError
lazy from snektest.runtime import current_backend, timeout
lazy from snektest.task_cleanup import cleanup_budget


async def _cancellation() -> BaseException:
    with trio.CancelScope() as scope:
        scope.cancel()
        try:
            await trio.lowlevel.checkpoint()
        except trio.Cancelled as error:
            return error
    msg = "Cancelled checkpoint returned without cancellation"
    raise RuntimeError(msg)


async def cancellation_error() -> BaseException:
    if current_backend() == "asyncio":
        return asyncio.CancelledError()
    return await _cancellation()


async def raise_cancelled(message: str) -> NoReturn:
    if current_backend() == "asyncio":
        raise asyncio.CancelledError(message)
    raise await _cancellation()


class ResultCell[T]:
    """A one-shot value; shielded waiters cannot cancel shared fixture setup."""

    def __init__(self) -> None:
        self._asyncio: asyncio.Future[T] | None = None
        self._event: trio.Event | None = None
        self._value: T | None = None
        self._error: BaseException | None = None
        self._cancelled = False
        if current_backend() == "asyncio":
            self._asyncio = asyncio.get_running_loop().create_future()
        else:
            self._event = trio.Event()

    def done(self) -> bool:
        if self._asyncio is not None:
            return self._asyncio.done()
        return self._event is not None and self._event.is_set()

    def cancelled(self) -> bool:
        return (
            self._asyncio.cancelled() if self._asyncio is not None else self._cancelled
        )

    def cancel(self) -> bool:
        if self._asyncio is not None:
            return self._asyncio.cancel()
        if self.done():
            return False
        self._cancelled = True
        if self._event is not None:
            self._event.set()
        return True

    def set_result(self, value: T) -> None:
        if self._asyncio is not None:
            self._asyncio.set_result(value)
            return
        if self.done():
            raise asyncio.InvalidStateError
        self._value = value
        if self._event is not None:
            self._event.set()

    def set_exception(self, error: BaseException) -> None:
        if self._asyncio is not None:
            self._asyncio.set_exception(error)
            return
        if self.done():
            raise asyncio.InvalidStateError
        self._error = error
        if self._event is not None:
            self._event.set()

    def result(self) -> T:
        if self._asyncio is not None:
            return self._asyncio.result()
        if not self.done() or self._cancelled:
            raise asyncio.InvalidStateError
        if self._error is not None:
            raise self._error
        return cast("T", self._value)

    async def wait(self) -> T:
        if self._asyncio is not None:
            return await asyncio.shield(self._asyncio)
        if self._event is not None:
            await self._event.wait()
        if self._cancelled:
            raise await _cancellation()
        return self.result()


class TrioTask[T]:
    def __init__(self, native: trio.lowlevel.Task, *, managed: bool = False) -> None:
        self.native = native
        self._managed = managed
        self._scope: trio.CancelScope | None = None
        self._cancel_requested = False
        self._done = False
        self._error: BaseException | None = None
        self._value: T | None = None
        self._callbacks: list[Callable[[Task[T]], object]] = []
        self._finished = trio.Event()

    def get_context(self) -> Context:
        return self.native.context

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return isinstance(self._error, trio.Cancelled)

    def cancelling(self) -> int:
        if self.native is trio.lowlevel.current_task():
            return int(trio.current_effective_deadline() == float("-inf"))
        return int(self._cancel_requested)

    def set_deadline(self, seconds: float) -> None:
        if self._scope is None:
            msg = "Managed task deadline set before task entry"
            raise RuntimeError(msg)
        self._scope.deadline = trio.current_time() + seconds

    def deadline_expired(self) -> bool:
        return (
            self._scope is not None
            and self._scope.cancel_called
            and not self._cancel_requested
            and trio.current_time() >= self._scope.deadline
        )

    def cancel(self) -> bool:
        if self.done() or not self._managed:
            return False
        self._cancel_requested = True
        if self._scope is not None:
            self._scope.cancel()
        return True

    def result(self) -> T:
        if not self.done():
            raise asyncio.InvalidStateError
        if self._error is not None:
            raise self._error
        return cast("T", self._value)

    def exception(self) -> BaseException | None:
        if not self.done():
            raise asyncio.InvalidStateError
        if self.cancelled() and self._error is not None:
            raise self._error
        return self._error

    def add_done_callback(self, callback: Callable[[Task[T]], object]) -> None:
        if self.done():
            _ = callback(self)
        else:
            self._callbacks.append(callback)

    def remove_done_callback(self, callback: Callable[[Task[T]], object]) -> int:
        before = len(self._callbacks)
        self._callbacks = [item for item in self._callbacks if item is not callback]
        return before - len(self._callbacks)

    def finish(self) -> None:
        self._done = True
        self._finished.set()
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                _ = callback(self)
            except BaseException as error:
                # A system-task exception must never escape into Trio's kernel.
                self._error = (
                    error
                    if self._error is None
                    else BaseExceptionGroup(
                        "Task and completion callback failed", [self._error, error]
                    )
                )

    async def wait(self) -> T:
        await self._finished.wait()
        return self.result()

    async def execute(self, coroutine: Coroutine[Any, Any, T]) -> None:
        try:
            with trio.CancelScope() as self._scope:
                if self._cancel_requested:
                    coroutine.close()
                    self._error = await _cancellation()
                else:
                    try:
                        self._value = await coroutine
                    except BaseException as error:
                        self._error = error
        except BaseException as error:
            self._error = error
        finally:
            self.finish()


type Task[T] = asyncio.Task[T] | TrioTask[T]


class _TrioTasks:
    def __init__(self) -> None:
        self.tasks: dict[trio.lowlevel.Task, TrioTask[Any]] = {}


def _instrument(state: _TrioTasks) -> trio.abc.Instrument:
    class Census(trio.abc.Instrument):
        @override
        def task_spawned(self, task: trio.lowlevel.Task) -> None:
            state.tasks[task] = TrioTask(task)

        @override
        def task_exited(self, task: trio.lowlevel.Task) -> None:
            handle = state.tasks.pop(task, None)
            if handle is not None:
                handle.finish()

    return Census()


_trio_tasks: ContextVar[_TrioTasks | None] = ContextVar(
    "snektest_trio_tasks", default=None
)


@contextmanager
def task_registry() -> Generator[None]:
    if current_backend() == "asyncio" or _trio_tasks.get() is not None:
        yield
        return
    state = _TrioTasks()
    task = trio.lowlevel.current_task()
    state.tasks[task] = TrioTask(task)
    instrument = _instrument(state)
    trio.lowlevel.add_instrument(instrument)
    token = _trio_tasks.set(state)
    try:
        yield
    finally:
        trio.lowlevel.remove_instrument(instrument)
        _trio_tasks.reset(token)


def spawn[T](
    coroutine: Coroutine[Any, Any, T],
    *,
    name: str | None = None,
    eager_start: bool | None = None,
) -> Task[T]:
    if current_backend() == "asyncio":
        return asyncio.create_task(coroutine, name=name, eager_start=eager_start)
    state = _trio_tasks.get()
    if state is None:
        coroutine.close()
        msg = "Trio task supervision requires a runtime scope"
        raise RuntimeError(msg)
    handle: TrioTask[T] | None = None

    async def execute() -> None:
        if handle is not None:
            await handle.execute(coroutine)

    try:
        native = trio.lowlevel.spawn_system_task(
            execute, name=name, context=copy_context()
        )
    except BaseException:
        coroutine.close()
        raise
    handle = TrioTask(native, managed=True)
    state.tasks[native] = handle
    return handle


def event() -> asyncio.Event | trio.Event:
    return asyncio.Event() if current_backend() == "asyncio" else trio.Event()


def current_task() -> Task[Any] | None:
    if current_backend() == "asyncio":
        return asyncio.current_task()
    state = _trio_tasks.get()
    return state.tasks.get(trio.lowlevel.current_task()) if state is not None else None


def all_tasks() -> set[Task[Any]]:
    if current_backend() == "asyncio":
        return set(asyncio.all_tasks())
    state = _trio_tasks.get()
    return set(state.tasks.values()) if state is not None else set()


async def shield[T](pending: Task[T] | ResultCell[T]) -> T:
    if isinstance(pending, TrioTask):
        return await cast("TrioTask[T]", pending).wait()
    if isinstance(pending, ResultCell):
        return await cast("ResultCell[T]", pending).wait()
    return await asyncio.shield(pending)


@contextmanager
def cancellation_shield(*, enabled: bool = True) -> Generator[None]:
    if current_backend() == "trio" and enabled:
        with trio.CancelScope(shield=True):
            yield
    else:
        yield


async def wait(
    tasks: Iterable[Task[Any]],
    *,
    timeout_seconds: float | None = None,
    first_completed: bool = False,
) -> tuple[set[Task[Any]], set[Task[Any]]]:
    pending = set(tasks)
    if not pending:
        return set(), set()
    if current_backend() == "asyncio":
        native = {task for task in pending if isinstance(task, asyncio.Task)}
        completed, unfinished = await asyncio.wait(
            native,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED
            if first_completed
            else asyncio.ALL_COMPLETED,
        )
        return set(completed), set(unfinished)
    return await _wait_trio(
        pending, timeout_seconds=timeout_seconds, first_completed=first_completed
    )


async def gather[T](
    *awaitables: Awaitable[T], return_exceptions: bool = False
) -> list[T | BaseException]:
    if current_backend() == "asyncio":
        return list(
            await asyncio.gather(*awaitables, return_exceptions=return_exceptions)
        )

    async def capture(awaitable: Awaitable[T]) -> tuple[bool, T | BaseException]:
        try:
            return True, await awaitable
        except BaseException as error:
            return False, error

    tasks = [spawn(capture(awaitable)) for awaitable in awaitables]
    try:
        outcomes = [await shield(task) for task in tasks]
        values: list[T | BaseException] = []
        for success, outcome in outcomes:
            if (
                not success
                and not return_exceptions
                and isinstance(outcome, BaseException)
            ):
                raise outcome
            values.append(outcome)
        return values
    finally:
        unfinished = {task for task in tasks if not task.done()}
        if unfinished:
            for task in unfinished:
                _ = task.cancel()
            with cancellation_shield():
                _, remaining = await wait(
                    unfinished, timeout_seconds=cleanup_budget.get()
                )
                if remaining:
                    msg = "Trio concurrent operations resisted cleanup; refusing to continue the run"
                    raise RunInfrastructureError(msg)


async def _wait_trio(
    pending: set[Task[Any]],
    *,
    timeout_seconds: float | None,
    first_completed: bool,
) -> tuple[set[Task[Any]], set[Task[Any]]]:
    changed = trio.Event()

    def wake(_: Task[Any]) -> None:
        changed.set()

    for task in pending:
        task.add_done_callback(wake)
    limit = timeout(timeout_seconds)
    try:
        async with limit:
            # A zero-time wait still gives runnable children one scheduling turn.
            if timeout_seconds == 0:
                await trio.lowlevel.cancel_shielded_checkpoint()
            while pending and not all(task.done() for task in pending):
                if first_completed and any(task.done() for task in pending):
                    break
                await changed.wait()
                changed = trio.Event()
    except TimeoutError:
        if not limit.expired():
            raise
    finally:
        for task in pending:
            _ = task.remove_done_callback(wake)
    return (
        {task for task in pending if task.done()},
        {task for task in pending if not task.done()},
    )
