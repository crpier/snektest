"""Fixture registry: per-run ownership of caching, setup, and teardown."""

import asyncio
import sys
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from inspect import isasyncgen, isgenerator, signature
from pickle import HIGHEST_PROTOCOL, dumps, loads
from types import TracebackType
from typing import Any, cast

from snektest.annotations import AsyncFixture, Coroutine, Fixture
from snektest.diagnostics import snapshot_exception
from snektest.models import (
    BadRequestError,
    FixtureError,
    TeardownFailure,
    UnreachableError,
)

type _SessionSlot = tuple[AsyncGenerator[Any] | Generator[Any], object, str]
type _RunFixtureHandle = Fixture[Any] | AsyncFixture[Any]
type _RunFixtureLoader = Callable[[_RunFixtureHandle], object]

_MAX_RUN_DESCRIPTOR_BYTES = 1024 * 1024
"""Largest serialized descriptor published by a run fixture."""


def _ensure_session_fixture_has_no_parameters(function: object, name: str) -> None:
    """Protect session fixture caching from call-argument-dependent values."""
    parameters = signature(cast("Callable[..., object]", function)).parameters
    if not parameters:
        return

    parameter_names = ", ".join(parameters)
    qualname = cast("str", getattr(function, "__qualname__", name))
    msg = (
        f"Session fixture {qualname} cannot accept parameters: {parameter_names}. "
        "Session fixtures are cached once per fixture function; use a function fixture for parameter-dependent setup, or return a factory/cache from a zero-argument session fixture."
    )
    raise FixtureError(msg)


@dataclass(frozen=True)
class _PendingAsyncSessionFixtureSetup:
    """Shared async fixture setup while the first load is still pending."""

    task: asyncio.Task[Any]


@dataclass(frozen=True)
class _PendingAsyncRunFixtureSetup:
    task: asyncio.Task[bytes]


@dataclass(frozen=True)
class _RunFixturePublicationFailure:
    message: str


async def teardown_fixture(
    fixture_name: str,
    generator: object,
    *,
    exc_info_provider: Callable[
        [], tuple[object | None, object | None, TracebackType | None]
    ] = sys.exc_info,
) -> TeardownFailure | None:
    """Advance one fixture (sync or async) through teardown, capturing failure."""
    try:
        if isasyncgen(generator):
            await anext(generator)
        elif isgenerator(generator):
            next(generator)
    except StopAsyncIteration, StopIteration:
        return None
    except Exception:
        exc_type, exc_value, traceback = exc_info_provider()
        if exc_type is None or exc_value is None or traceback is None:
            msg = "Invalid exception info gathered during teardown. This shouldn't be possible!"
            raise UnreachableError(msg) from None
        return TeardownFailure(
            exception=snapshot_exception(
                cast("type[BaseException]", exc_type),
                cast("BaseException", exc_value),
                traceback,
            ),
            fixture_name=fixture_name,
        )
    else:
        msg = f"Incorrect fixture function {fixture_name} yielded more than once"
        raise BadRequestError(msg)


class FixtureRegistry:
    """Owns all fixture state and teardown for a single test run.

    A fresh registry is created per run and reached ambiently through a
    `ContextVar`. It caches session fixtures (keyed by the decorated function),
    tracks active function fixtures for first-in-last-out teardown, and drives
    the concurrent-first-await machinery for async session fixtures.
    """

    def __init__(self) -> None:
        self._session: dict[object, _SessionSlot] = {}
        self._session_order: list[object] = []
        self._pending_session_tasks: set[asyncio.Task[Any]] = set()
        self._run: dict[object, _SessionSlot] = {}
        self._run_copies: dict[object, object] = {}
        self._run_order: list[object] = []
        self._pending_run_tasks: set[asyncio.Task[bytes]] = set()
        self._function_stack: list[
            tuple[str, AsyncGenerator[Any] | Generator[Any]]
        ] = []
        self._loading_session: ContextVar[str | None] = ContextVar(
            f"snektest_loading_session_{id(self)}", default=None
        )
        self._loading_run: ContextVar[str | None] = ContextVar(
            f"snektest_loading_run_{id(self)}", default=None
        )
        self._setup_stack: ContextVar[tuple[tuple[object, str], ...]] = ContextVar(
            f"snektest_fixture_setup_stack_{id(self)}", default=()
        )
        self._tearing_down = False

    def _ensure_loading_allowed(self) -> None:
        if self._tearing_down:
            msg = "Fixtures cannot be loaded after fixture teardown has started"
            raise FixtureError(msg)

    def _reject_dependency_cycle(self, key: object, name: str) -> None:
        stack = self._setup_stack.get()
        if not any(active_key is key for active_key, _ in stack):
            return
        cycle_start = next(
            index for index, (active_key, _) in enumerate(stack) if active_key is key
        )
        cycle_names = [active_name for _, active_name in stack[cycle_start:]]
        cycle = " -> ".join([*cycle_names, name])
        msg = f"Fixture dependency cycle detected: {cycle}"
        raise FixtureError(msg)

    @contextmanager
    def _fixture_setup_scope(self, key: object, name: str) -> Generator[None]:
        """Track setup dependencies and reject cycles before recursing."""
        stack = self._setup_stack.get()
        self._reject_dependency_cycle(key, name)
        token = self._setup_stack.set((*stack, (key, name)))
        try:
            yield
        finally:
            self._setup_stack.reset(token)

    @contextmanager
    def _session_setup_scope(self, name: str) -> Generator[None]:
        """Mark that a session fixture is mid-setup, to forbid function deps."""
        token = self._loading_session.set(name)
        try:
            yield
        finally:
            self._loading_session.reset(token)

    @contextmanager
    def _run_setup_scope(self, name: str) -> Generator[None]:
        """Mark run setup so shorter-lived dependencies can be rejected."""
        token = self._loading_run.set(name)
        try:
            yield
        finally:
            self._loading_run.reset(token)

    def load_function[R](
        self, handle: Fixture[R] | AsyncFixture[R]
    ) -> R | Coroutine[R]:
        """Set up a function-scoped fixture and register it for teardown.

        A fixture may depend on another by calling `load_fixture` in its body.
        The dependency is registered for teardown only after its own setup
        completes, so it lands below the depending fixture on the teardown stack
        and is torn down *after* it (the depending fixture may use the dependency
        during teardown). A session fixture may not depend on a function fixture:
        the function fixture is torn down after each test while the session
        fixture outlives it, so the dependency is rejected here.
        """
        self._ensure_loading_allowed()
        self._reject_dependency_cycle(handle.key, handle.name)
        loading_parent = self._loading_session.get() or self._loading_run.get()
        if loading_parent is not None:
            msg = (
                f"Cached fixture {loading_parent} cannot depend on function "
                f"fixture {handle.name}. A function fixture is torn down after each "
                "test, but the session fixture outlives it and would reference "
                "torn-down state. Make the dependency a session fixture instead."
            )
            raise FixtureError(msg)
        if isinstance(handle, AsyncFixture):
            agen = handle.make()
            return cast(
                "Coroutine[R]",
                self._setup_async_function(handle.key, handle.name, agen),
            )
        gen = handle.make()
        with self._fixture_setup_scope(handle.key, handle.name):
            value = next(gen)
        self._function_stack.append((handle.name, gen))
        return value

    async def _setup_async_function[R](
        self, key: object, name: str, agen: AsyncGenerator[R]
    ) -> R:
        """Await an async function fixture's setup, then register its teardown."""
        with self._fixture_setup_scope(key, name):
            value = await agen.__anext__()
        self._function_stack.append((name, agen))
        return value

    def load_session[R](self, handle: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
        """Set up a session-scoped fixture once and reuse it thereafter."""
        self._ensure_loading_allowed()
        self._reject_dependency_cycle(handle.key, handle.name)
        if (loading_run := self._loading_run.get()) is not None:
            msg = (
                f"Run fixture {loading_run} cannot depend on session fixture "
                f"{handle.name}; the run fixture outlives each worker session."
            )
            raise FixtureError(msg)
        if isinstance(handle, AsyncFixture):
            return self._load_session_async(handle)
        return self._load_session_sync(handle)

    def load_run[R](self, handle: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
        """Load a local worker copy of one host-owned run descriptor."""
        self._ensure_loading_allowed()
        self._reject_dependency_cycle(handle.key, handle.name)
        if handle.key in self._run_copies:
            cached_copy = cast("R", self._run_copies[handle.key])
            if isinstance(handle, AsyncFixture):
                return self._wrap_async_session_result(cached_copy)
            return cached_copy
        payload = self.load_run_payload(handle)
        if isinstance(handle, AsyncFixture):

            async def decode() -> R:
                serialized = await cast("Coroutine[bytes]", payload)
                if handle.key in self._run_copies:
                    return cast("R", self._run_copies[handle.key])
                descriptor = cast("R", loads(serialized))  # noqa: S301
                self._run_copies[handle.key] = descriptor
                return descriptor

            return cast("Coroutine[R]", decode())
        descriptor = cast("R", loads(cast("bytes", payload)))  # noqa: S301
        self._run_copies[handle.key] = descriptor
        return descriptor

    def load_run_payload(self, handle: _RunFixtureHandle) -> bytes | Coroutine[bytes]:
        """Set up one run fixture and return its validated descriptor bytes."""
        self._ensure_loading_allowed()
        self._reject_dependency_cycle(handle.key, handle.name)
        slot = self._run.get(handle.key)
        if slot is not None:
            cached = slot[1]
            if isinstance(cached, _RunFixturePublicationFailure):
                raise FixtureError(cached.message)
            if isinstance(cached, _PendingAsyncRunFixtureSetup):
                return cast("Coroutine[bytes]", self._await_run_setup(cached.task))
            return cast("bytes", cached)
        if isinstance(handle, AsyncFixture):
            return self._create_async_run_setup(handle)
        return self._create_sync_run_setup(handle)

    def _create_sync_run_setup(self, handle: Fixture[Any]) -> bytes:
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        generator = handle.make()
        try:
            with (
                self._fixture_setup_scope(handle.key, handle.name),
                self._run_setup_scope(handle.name),
            ):
                descriptor = next(generator)
            payload = self._serialize_run_descriptor(handle.name, descriptor)
        except BaseException as exc:
            message = f"Run fixture {handle.name} publication failed: {type(exc).__name__}: {exc}"
            failure = _RunFixturePublicationFailure(message)
            self._run[handle.key] = (generator, failure, handle.name)
            self._run_order.append(handle.key)
            raise FixtureError(message) from None
        self._run[handle.key] = (generator, payload, handle.name)
        self._run_order.append(handle.key)
        return payload

    def _create_async_run_setup(self, handle: AsyncFixture[Any]) -> Coroutine[bytes]:
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        generator = handle.make()

        async def setup() -> bytes:
            try:
                with (
                    self._fixture_setup_scope(handle.key, handle.name),
                    self._run_setup_scope(handle.name),
                ):
                    descriptor = await anext(generator)
                payload = self._serialize_run_descriptor(handle.name, descriptor)
            except BaseException as exc:
                message = f"Run fixture {handle.name} publication failed: {type(exc).__name__}: {exc}"
                failure = _RunFixturePublicationFailure(message)
                self._run[handle.key] = (generator, failure, handle.name)
                self._run_order.append(handle.key)
                raise FixtureError(message) from None
            self._run[handle.key] = (generator, payload, handle.name)
            self._run_order.append(handle.key)
            return payload

        task = asyncio.create_task(setup())
        self._pending_run_tasks.add(task)
        task.add_done_callback(self._run_setup_finished)
        self._run[handle.key] = (
            generator,
            _PendingAsyncRunFixtureSetup(task),
            handle.name,
        )
        return cast("Coroutine[bytes]", self._await_run_setup(task))

    @staticmethod
    def _serialize_run_descriptor(name: str, descriptor: object) -> bytes:
        payload = dumps(descriptor, protocol=HIGHEST_PROTOCOL)
        if len(payload) > _MAX_RUN_DESCRIPTOR_BYTES:
            msg = (
                f"Run fixture {name} descriptor is {len(payload)} bytes; "
                f"the limit is {_MAX_RUN_DESCRIPTOR_BYTES} bytes"
            )
            raise FixtureError(msg)
        return payload

    def _run_setup_finished(self, task: asyncio.Task[bytes]) -> None:
        self._pending_run_tasks.discard(task)
        if not task.cancelled():
            _ = task.exception()

    async def _await_run_setup(self, task: asyncio.Task[bytes]) -> bytes:
        return await asyncio.shield(task)

    def _load_session_sync[R](self, handle: Fixture[R]) -> R:
        slot = self._session.get(handle.key)
        if slot is not None:
            return cast("R", slot[1])
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        gen = handle.make()
        with (
            self._fixture_setup_scope(handle.key, handle.name),
            self._session_setup_scope(handle.name),
        ):
            value = next(gen)
        self._session[handle.key] = (gen, value, handle.name)
        self._session_order.append(handle.key)
        return value

    def _load_session_async[R](self, handle: AsyncFixture[R]) -> Coroutine[R]:
        slot = self._session.get(handle.key)
        if slot is None:
            _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
            agen = handle.make()
            return self._create_async_session_setup(handle.key, handle.name, agen)
        cached = slot[1]
        if isinstance(cached, _PendingAsyncSessionFixtureSetup):
            return cast("Coroutine[R]", self._await_async_session_setup(cached.task))
        return self._wrap_async_session_result(cast("R", cached))

    def _create_async_session_setup[R](
        self, key: object, name: str, agen: AsyncGenerator[R]
    ) -> Coroutine[R]:
        async def result_updater() -> R:
            setup_completed = False
            try:
                with (
                    self._fixture_setup_scope(key, name),
                    self._session_setup_scope(name),
                ):
                    result = await anext(agen)
                setup_completed = True
            finally:
                if not setup_completed:
                    self._session.pop(key, None)

            self._session[key] = (agen, result, name)
            self._session_order.append(key)
            return result

        task = asyncio.create_task(result_updater())
        self._pending_session_tasks.add(task)
        task.add_done_callback(self._session_setup_finished)
        self._session[key] = (agen, _PendingAsyncSessionFixtureSetup(task), name)
        return cast("Coroutine[R]", self._await_async_session_setup(task))

    def _session_setup_finished(self, task: asyncio.Task[Any]) -> None:
        """Consume unobserved setup errors while preserving them for awaiters."""
        self._pending_session_tasks.discard(task)
        if not task.cancelled():
            _ = task.exception()

    async def _await_async_session_setup[R](self, task: asyncio.Task[Any]) -> R:
        """Shield shared setup so cancelling one waiter cannot cancel its owner."""
        return cast("R", await asyncio.shield(task))

    def _wrap_async_session_result[R](self, result: R) -> Coroutine[R]:
        async def wrapper() -> R:
            return result

        return cast("Coroutine[R]", wrapper())

    async def teardown_function_fixtures(self) -> list[TeardownFailure]:
        """Tear down active function fixtures in first-in-last-out order."""
        self._tearing_down = True
        try:
            failures: list[TeardownFailure] = []
            for fixture_name, generator in reversed(self._function_stack):
                failure = await teardown_fixture(fixture_name, generator)
                if failure is not None:
                    failures.append(failure)
            self._function_stack.clear()
            return failures
        finally:
            self._tearing_down = False

    async def teardown_session_fixtures(self) -> list[TeardownFailure]:
        """Tear down session fixtures in reverse setup order."""
        self._tearing_down = True
        try:
            pending_tasks = tuple(self._pending_session_tasks)
            for task in pending_tasks:
                _ = task.cancel()
            if pending_tasks:
                _ = await asyncio.gather(*pending_tasks, return_exceptions=True)

            failures: list[TeardownFailure] = []
            for key in reversed(self._session_order):
                generator, cached, name = self._session[key]
                if isinstance(cached, _PendingAsyncSessionFixtureSetup):
                    continue
                failure = await teardown_fixture(name, generator)
                if failure is not None:
                    failures.append(failure)
            self._session.clear()
            self._session_order.clear()
            return failures
        finally:
            self._tearing_down = False

    async def teardown_run_fixtures(self) -> list[TeardownFailure]:
        """Tear down host-owned run fixtures in reverse dependency order."""
        self._tearing_down = True
        try:
            pending_tasks = tuple(self._pending_run_tasks)
            for task in pending_tasks:
                _ = task.cancel()
            if pending_tasks:
                _ = await asyncio.gather(*pending_tasks, return_exceptions=True)

            failures: list[TeardownFailure] = []
            for key in reversed(self._run_order):
                generator, cached, name = self._run[key]
                if isinstance(cached, _PendingAsyncRunFixtureSetup):
                    continue
                failure = await teardown_fixture(name, generator)
                if failure is not None:
                    failures.append(failure)
            self._run.clear()
            self._run_copies.clear()
            self._run_order.clear()
            return failures
        finally:
            self._tearing_down = False


_current_registry: ContextVar[FixtureRegistry] = ContextVar("snektest_fixture_registry")
_current_run_fixture_loader: ContextVar[_RunFixtureLoader | None] = ContextVar(
    "snektest_run_fixture_loader", default=None
)


def current_registry() -> FixtureRegistry:
    """Return the fixture registry for the current run."""
    try:
        return _current_registry.get()
    except LookupError:
        msg = "No active fixture registry. `load_fixture` must be called during a snektest run."
        raise UnreachableError(msg) from None


def load_run_fixture[R](
    handle: Fixture[R] | AsyncFixture[R],
) -> R | Coroutine[R]:
    """Load a run descriptor through the process provider or local registry."""
    if (loader := _current_run_fixture_loader.get()) is not None:
        return cast("R | Coroutine[R]", loader(handle))
    return cast("R | Coroutine[R]", current_registry().load_run(handle))


@contextmanager
def use_run_fixture_loader(loader: _RunFixtureLoader) -> Generator[None]:
    """Route run fixture loads through a worker's coordinator connection."""
    token = _current_run_fixture_loader.set(loader)
    try:
        yield
    finally:
        _current_run_fixture_loader.reset(token)


@contextmanager
def use_registry(registry: FixtureRegistry) -> Generator[FixtureRegistry]:
    """Bind a fixture registry for the duration of a run."""
    token = _current_registry.set(registry)
    try:
        yield registry
    finally:
        _current_registry.reset(token)
