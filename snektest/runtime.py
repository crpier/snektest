"""Private native-runtime primitives shared by execution and IO adapters.

The execution choice is explicit and run-local; embedding hosts must match it.
Startup IO and assertion probes can observe a host without selecting an engine.
Managed tasks and native inventory live in runtime_tasks; no nested loop starts.
"""

lazy import asyncio
lazy import sys
lazy from collections.abc import Callable, Coroutine, Generator
lazy from contextlib import contextmanager
lazy from contextvars import ContextVar, copy_context
lazy from functools import partial
lazy from types import TracebackType
lazy from typing import Literal, Protocol, Self, override

lazy import trio

lazy from snektest.models import BadRequestError, UnreachableError
lazy from snektest.runtime_tasks import task_registry

type Backend = Literal["asyncio", "trio"]

_backend: ContextVar[Backend] = ContextVar("snektest_backend", default="asyncio")


def backend_name(value: str) -> Backend:
    """Validate an explicit backend name without importing optional engines."""
    if value == "asyncio":
        return "asyncio"
    if value == "trio":
        return "trio"
    msg = f"Unsupported async backend {value!r}; choose 'asyncio' or 'trio'"
    raise BadRequestError(msg)


def require_backend(selected: Backend) -> None:
    if selected == "trio":
        _require_trio()


def host_cancelled_errors() -> tuple[type[BaseException], ...]:
    """Classify cancellation before binding or after retiring a native runtime."""
    if "trio" in sys.modules:
        return (asyncio.CancelledError, trio.Cancelled)
    return (asyncio.CancelledError,)


async def host_run_sync[T](
    function: Callable[..., T], /, *args: object, **kwargs: object
) -> T:
    """Read startup configuration on the actual host; never select an engine."""
    call = partial(function, *args, **kwargs)
    if _asyncio_running():
        return await asyncio.to_thread(call)
    if _trio_running():
        return await trio.to_thread.run_sync(call, abandon_on_cancel=True)
    msg = "Startup configuration requires a running async runtime"
    raise BadRequestError(msg)


def current_backend() -> Backend:
    return _backend.get()


def current_time() -> float:
    if current_backend() == "asyncio":
        return asyncio.get_running_loop().time()
    return trio.current_time()


def _require_trio() -> None:
    try:
        _ = trio.__version__
    except ModuleNotFoundError as error:
        if error.name != "trio":
            raise
        msg = "The Trio backend requires the optional Trio dependency"
        raise BadRequestError(msg) from error


def _asyncio_running() -> bool:
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _trio_running() -> bool:
    if "trio" not in sys.modules:
        return False
    try:
        _ = trio.lowlevel.current_trio_token()
    except RuntimeError:
        return False
    return True


@contextmanager
def bootstrap_runtime(backend: Backend) -> Generator[None]:
    """Bind a spawned child's explicit setting before its async entry exists."""
    selected = backend_name(backend)
    if selected == "trio":
        _require_trio()
    token = _backend.set(selected)
    try:
        yield
    finally:
        _backend.reset(token)


@contextmanager
def runtime_scope(backend: Backend) -> Generator[None]:
    """Validate the caller's runtime before binding a command's explicit choice."""
    selected = backend_name(backend)
    if selected == "trio":
        _require_trio()
        matching = _trio_running() and not _asyncio_running()
    else:
        matching = _asyncio_running() and not _trio_running()
    if not matching:
        msg = f"The {selected} backend requires a matching running {selected} runtime"
        raise BadRequestError(msg)
    token = _backend.set(selected)
    try:
        with task_registry():
            yield
    finally:
        _backend.reset(token)


def run[T](
    async_fn: Callable[[], Coroutine[object, object, T]], *, backend: Backend
) -> T:
    """Start a standalone native runtime; a rejected call creates no coroutine."""
    selected = backend_name(backend)
    if _asyncio_running() or _trio_running():
        msg = "Cannot start a Snektest runtime inside a running async runtime"
        raise BadRequestError(msg)
    if selected == "trio":
        _require_trio()

    async def root() -> T:
        with runtime_scope(selected):
            return await async_fn()

    if selected == "asyncio":
        return asyncio.run(root())
    return trio.run(root)


async def run_sync[**P, T](
    function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    """Offload with copied context; cancellation never claims to stop the thread.

    Like asyncio.to_thread, the Trio adapter allows cancellation of the waiter.
    Protocol callers must separately retain/drain outstanding reads. In Trio,
    abandoned jobs may outlive the native runtime rather than delay its shutdown.
    """
    if current_backend() == "asyncio":
        return await asyncio.to_thread(function, *args, **kwargs)
    return await trio.to_thread.run_sync(
        partial(function, *args, **kwargs), abandon_on_cancel=True
    )


def threadsafe_scheduler() -> Callable[[Callable[[], object]], None]:
    """Submit with the sending thread's current example/run/owner context."""
    if current_backend() == "asyncio":
        loop = asyncio.get_running_loop()

        def schedule(callback: Callable[[], object]) -> None:
            _ = loop.call_soon_threadsafe(callback)

        return schedule
    token = trio.lowlevel.current_trio_token()

    def schedule_trio(callback: Callable[[], object]) -> None:
        token.run_sync_soon(copy_context().run, callback)

    return schedule_trio


def cancelled_errors() -> tuple[type[BaseException], ...]:
    """Return only the explicitly selected runtime's cancellation class."""
    if current_backend() == "asyncio":
        return (asyncio.CancelledError,)
    return (trio.Cancelled,)


class CheckpointProbe(Protocol):
    def cancel(self) -> None: ...


def next_checkpoint(callback: Callable[[], None]) -> CheckpointProbe | None:
    """Observe the actual host for standalone assertions, not select a runner."""
    if _asyncio_running():
        return asyncio.get_running_loop().call_soon(callback)
    if not _trio_running():
        return None

    class Probe(trio.abc.Instrument):
        def __init__(self, fire: Callable[[], None]) -> None:
            self.fire: Callable[[], None] | None = fire
            self.task: trio.lowlevel.Task | None = trio.lowlevel.current_task()
            trio.lowlevel.add_instrument(self)

        @override
        def after_task_step(self, task: trio.lowlevel.Task) -> None:
            if task is self.task and self.fire is not None:
                fire, self.fire = self.fire, None
                fire()

        def cancel(self) -> None:
            trio.lowlevel.remove_instrument(self)
            self.task = None
            self.fire = None

    return Probe(callback)


class Deadline(Protocol):
    """Async timeout context that distinguishes its own expiry from user errors."""

    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...

    def expired(self) -> bool: ...


class _TrioDeadline:
    def __init__(self, seconds: float | None) -> None:
        self._seconds = seconds
        self._scope: trio.CancelScope | None = None

    async def __aenter__(self) -> Self:
        deadline = (
            float("inf")
            if self._seconds is None
            else trio.current_time() + self._seconds
        )
        self._scope = trio.CancelScope(deadline=deadline)
        _ = self._scope.__enter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._scope is None:
            msg = "Runtime deadline exited before entry"
            raise UnreachableError(msg)
        suppressed = self._scope.__exit__(exc_type, exc_value, traceback)
        if self._scope.cancelled_caught:
            raise TimeoutError from None
        return suppressed

    def expired(self) -> bool:
        return self._scope is not None and self._scope.cancel_called


def timeout(seconds: float | None) -> Deadline:
    """Translate only this deadline's cancellation into stdlib TimeoutError."""
    if current_backend() == "asyncio":
        return asyncio.timeout(seconds)
    return _TrioDeadline(seconds)
