"""Fixture registry: per-run ownership of caching, setup, and teardown."""

lazy import sys
lazy from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Generator,
    Iterable,
)
lazy from contextlib import contextmanager
lazy from contextvars import ContextVar
lazy from dataclasses import dataclass
lazy from inspect import isasyncgen, isfunction, isgenerator, signature
lazy from os import PathLike
lazy from pathlib import Path
lazy from pickle import HIGHEST_PROTOCOL, dumps, loads
lazy from types import TracebackType
lazy from typing import Any, Literal, cast

lazy from snektest.annotations import AsyncFixture, Coroutine, Fixture
lazy from snektest.async_fixtures import AsyncFixtureDriver
lazy from snektest.diagnostics import snapshot_exception
lazy from snektest.models import (
    DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    BadRequestError,
    FixtureError,
    FixtureTaskLeakError,
    FixtureTeardownTimeoutError,
    RunInfrastructureError,
    TeardownFailure,
    UnreachableError,
)
lazy from snektest.progress import activity
lazy from snektest.runtime import cancelled_errors
lazy from snektest.runtime import timeout as runtime_timeout
lazy from snektest.runtime_tasks import Task
lazy from snektest.task_cleanup import (
    cancel_owned_tasks,
    cancel_tasks,
    defer_cancellation,
    task_scope,
)
lazy from snektest.temporary import TemporaryDirectories

type _FixtureGenerator = AsyncFixtureDriver[Any] | AsyncGenerator[Any] | Generator[Any]
type _PendingSetup = Task[Any] | AsyncFixtureDriver[Any]
type _SessionSlot = tuple[_FixtureGenerator, object, str]
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

    driver: AsyncFixtureDriver[Any]


@dataclass(frozen=True)
class _PendingAsyncRunFixtureSetup:
    driver: AsyncFixtureDriver[bytes]


@dataclass(frozen=True)
class _RunFixturePublicationFailure:
    message: str


@dataclass(frozen=True)
class _RunFixtureInterruption:
    """Carry setup interruption through a child task without stopping its loop."""

    interruption: BaseException


async def _teardown_async_fixture(
    fixture_name: str,
    generator: AsyncIterator[Any],
    cleanup_timeout: float,
) -> None:
    """Advance async teardown and distinguish its own timeout from user errors."""
    cancel_scope = runtime_timeout(cleanup_timeout)
    try:
        async with cancel_scope:
            await anext(generator)
    except TimeoutError:
        if cancel_scope.expired():
            raise FixtureTeardownTimeoutError(fixture_name, cleanup_timeout) from None
        raise


async def teardown_fixture(
    fixture_name: str,
    generator: object,
    *,
    cleanup_timeout: float = DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    scope: str | None = None,
    exc_info_provider: Callable[
        [], tuple[object | None, object | None, TracebackType | None]
    ] = sys.exc_info,
) -> TeardownFailure | None:
    """Advance one fixture (sync or async) through teardown, capturing failure."""
    try:
        with (
            task_scope(generator),
            activity("fixture_teardown", fixture=fixture_name, scope=scope),
        ):
            if isinstance(generator, AsyncFixtureDriver):
                await generator.teardown(cleanup_timeout)
                return None
            if isasyncgen(generator):
                await _teardown_async_fixture(fixture_name, generator, cleanup_timeout)
            elif isgenerator(generator):
                next(generator)
        msg = f"Incorrect fixture function {fixture_name} yielded more than once"
        raise BadRequestError(msg)  # noqa: TRY301
    except StopAsyncIteration, StopIteration:
        return None
    except RunInfrastructureError:
        raise
    except Exception, BadRequestError:
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


async def _cleanup_fixture_tasks(
    fixture_name: str,
    owner: object,
    *,
    cleanup_timeout: float,
) -> list[TeardownFailure]:
    """Cancel tasks abandoned by one fixture and attribute the failure."""

    identity = owner.generator if isinstance(owner, AsyncFixtureDriver) else owner
    try:
        cleanup = await cancel_owned_tasks(identity, timeout=cleanup_timeout)
    finally:
        if isinstance(owner, AsyncFixtureDriver):
            owner.flush_warnings()
    if not cleanup.total:
        return []

    try:
        raise FixtureTaskLeakError(fixture_name, cleanup.total)  # noqa: TRY301
    except FixtureTaskLeakError as error:
        traceback = error.__traceback__
        if traceback is None:
            msg = "Fixture task leak had no traceback. This shouldn't be possible!"
            raise UnreachableError(msg) from None
        return [
            TeardownFailure(
                exception=snapshot_exception(type(error), error, traceback),
                fixture_name=fixture_name,
            ),
            *(
                TeardownFailure(exception=diagnostic, fixture_name=fixture_name)
                for diagnostic in cleanup.failures
            ),
        ]


def _request_setup_cancellation(
    pending: Iterable[tuple[_PendingSetup, str]],
) -> list[tuple[Task[Any], str]]:
    setups: list[tuple[Task[Any], str]] = []
    for item, name in tuple(pending):
        task = item.task if isinstance(item, AsyncFixtureDriver) else item
        if task.done():
            continue
        setups.append((task, name))
        if isinstance(item, AsyncFixtureDriver):
            item.cancel_setup()
        else:
            _ = task.cancel()
    return setups


async def _cancel_pending_setups(
    pending: Iterable[tuple[_PendingSetup, str]],
    *,
    cleanup_timeout: float,
    abandoned: bool = False,
) -> list[TeardownFailure]:
    """Bound unfinished setup before tearing down its established dependencies.

    Cancelling all setups first releases shared dependency waits. Cooperative
    cancellation is normal shutdown for cached fixtures. Function setup left
    pending when its test returns is abandoned work. Resistant setup and cleanup
    errors are fixture teardown failures. Descendants are reaped with their fixture
    after its setup has unwound and registered its teardown attempt.
    """
    setups = _request_setup_cancellation(pending)
    failures: list[TeardownFailure] = []
    for task, name in setups:
        cleanup = await cancel_tasks({task}, timeout=cleanup_timeout)
        cleanup_error: BaseException | None = None
        if cleanup.resistant:
            cleanup_error = FixtureTeardownTimeoutError(name, cleanup_timeout)
        elif not task.cancelled():
            cleanup_error = task.exception()
            if cleanup_error is None:
                outcome: object = task.result()
                if isinstance(outcome, BaseException):
                    cleanup_error = outcome
        if cleanup_error is None and abandoned:
            cleanup_error = FixtureTaskLeakError(name, 1)
        if cleanup_error is not None:
            try:
                raise cleanup_error  # noqa: TRY301
            except BaseException as error:
                failures.append(
                    TeardownFailure(
                        exception=snapshot_exception(
                            type(error), error, error.__traceback__
                        ),
                        fixture_name=name,
                    )
                )
        failures.extend(
            TeardownFailure(exception=diagnostic, fixture_name=name)
            for diagnostic in cleanup.failures
        )
    return failures


class FixtureRegistry:
    """Owns all fixture state and teardown for a single test run.

    A fresh registry is created per run and reached ambiently through a
    `ContextVar`. It caches session fixtures (keyed by the decorated function),
    tracks active function fixtures for first-in-last-out teardown, and drives
    the concurrent-first-await machinery for async session fixtures.
    """

    def __init__(self) -> None:
        self.temporary_directories = TemporaryDirectories()
        self._pending_function_tasks: dict[_PendingSetup, str] = {}
        self._session: dict[object, _SessionSlot] = {}
        # Cache entries can be retried; every attempted generator retains cleanup.
        self._session_stack: list[tuple[str, _FixtureGenerator]] = []
        self._pending_session_tasks: dict[_PendingSetup, str] = {}
        self._run: dict[object, _SessionSlot] = {}
        self._run_copies: dict[object, object] = {}
        self._run_order: list[object] = []
        self._pending_run_tasks: dict[_PendingSetup, str] = {}
        self._function_stack: list[tuple[str, _FixtureGenerator]] = []
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
        self._function_teardown_started = False

    def start_test(self) -> None:
        """Open a fresh function lifetime before executing the next test."""
        self._function_teardown_started = False
        self.temporary_directories.start_test()

    def _ensure_function_loading_allowed(self) -> None:
        self._ensure_loading_allowed()
        if self._function_teardown_started:
            msg = "Fixtures cannot be loaded after fixture teardown has started"
            raise FixtureError(msg)

    def allocate_temporary_directory(
        self,
        *,
        base: str | PathLike[str] | None,
        retention: Literal["never", "on-failure", "always"],
    ) -> Path:
        self._ensure_loading_allowed()
        if self._loading_run.get() is not None:
            msg = "temporary_directory supports test/function and session lifetimes, not run fixtures"
            raise FixtureError(msg)
        return self.temporary_directories.allocate(
            base, retention, session=self._loading_session.get() is not None
        )

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
    def _fixture_setup_scope(
        self, key: object, name: str, scope: str
    ) -> Generator[None]:
        """Track setup dependencies and reject cycles before recursing."""
        stack = self._setup_stack.get()
        self._reject_dependency_cycle(key, name)
        token = self._setup_stack.set((*stack, (key, name)))
        try:
            with activity(
                "fixture_setup",
                fixture=name,
                scope=scope,
                file=key.__code__.co_filename if isfunction(key) else None,
            ):
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
        The dependency is registered for teardown after its own setup attempt
        finishes, so it lands below the depending fixture on the teardown stack
        and is torn down *after* it (the depending fixture may use the dependency
        during teardown). A session fixture may not depend on a function fixture:
        the function fixture is torn down after each test while the session
        fixture outlives it, so the dependency is rejected here.
        """
        self._ensure_function_loading_allowed()
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
        with (
            self._fixture_setup_scope(handle.key, handle.name, "function"),
            task_scope(gen),
        ):
            try:
                value = next(gen)
            finally:
                self._function_stack.append((handle.name, gen))
        return value

    async def _setup_async_function[R](
        self, key: object, name: str, agen: AsyncGenerator[R]
    ) -> R:
        """Retain unfinished setup and keep its generator in one owning task."""

        self._ensure_function_loading_allowed()

        async def setup() -> R:
            with self._fixture_setup_scope(key, name, "function"):
                try:
                    return await anext(agen)
                finally:
                    self._function_stack.append((name, driver))
                    _ = self._pending_function_tasks.pop(driver, None)

        driver = AsyncFixtureDriver(name, agen, setup)
        self._pending_function_tasks[driver] = name
        driver.task.add_done_callback(
            lambda _: self._pending_function_tasks.pop(driver, None)
        )
        return await driver.wait_setup(cancel_with_waiter=True)

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
            return self._load_session_async(cast("AsyncFixture[R]", handle))
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
            if isinstance(cached, _RunFixtureInterruption):
                raise cached.interruption
            if isinstance(cached, _RunFixturePublicationFailure):
                raise FixtureError(cached.message)
            if isinstance(cached, _PendingAsyncRunFixtureSetup):
                return cast("Coroutine[bytes]", self._await_run_setup(cached.driver))
            return cast("bytes", cached)
        if isinstance(handle, AsyncFixture):
            return self._create_async_run_setup(handle)
        return self._create_sync_run_setup(handle)

    def _create_sync_run_setup(self, handle: Fixture[Any]) -> bytes:
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        generator = handle.make()
        try:
            with (
                self._fixture_setup_scope(handle.key, handle.name, "run"),
                self._run_setup_scope(handle.name),
                task_scope(generator),
            ):
                descriptor = next(generator)
            payload = self._serialize_run_descriptor(handle.name, descriptor)
        except BaseException as exc:
            failure = self._record_run_setup_failure(handle, generator, exc)
            if isinstance(failure, _RunFixtureInterruption):
                raise
            raise FixtureError(failure.message) from None
        self._run[handle.key] = (generator, payload, handle.name)
        self._run_order.append(handle.key)
        return payload

    def _create_async_run_setup(self, handle: AsyncFixture[Any]) -> Coroutine[bytes]:
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        generator = handle.make()

        async def setup() -> bytes:
            try:
                with (
                    self._fixture_setup_scope(handle.key, handle.name, "run"),
                    self._run_setup_scope(handle.name),
                ):
                    descriptor = await anext(generator)
                payload = self._serialize_run_descriptor(handle.name, descriptor)
            except BaseException as exc:
                failure = self._record_run_setup_failure(handle, driver, exc)
                if isinstance(exc, GeneratorExit) or isinstance(
                    failure, _RunFixtureInterruption
                ):
                    raise
                raise FixtureError(failure.message) from None
            finally:
                _ = self._pending_run_tasks.pop(driver, None)
            self._run[handle.key] = (driver, payload, handle.name)
            self._run_order.append(handle.key)
            return payload

        driver = AsyncFixtureDriver[bytes](handle.name, generator, setup)
        self._pending_run_tasks[driver] = handle.name
        self._run[handle.key] = (
            driver,
            _PendingAsyncRunFixtureSetup(driver),
            handle.name,
        )
        return cast("Coroutine[bytes]", self._await_run_setup(driver))

    def _record_run_setup_failure(
        self,
        handle: _RunFixtureHandle,
        generator: _FixtureGenerator,
        error: BaseException,
    ) -> _RunFixtureInterruption | _RunFixturePublicationFailure:
        """Retain cleanup ownership without relabeling interruption as publication."""
        failure: _RunFixtureInterruption | _RunFixturePublicationFailure
        if isinstance(error, (SystemExit, KeyboardInterrupt, *cancelled_errors())):
            failure = _RunFixtureInterruption(error)
        else:
            diagnostic = snapshot_exception(type(error), error, error.__traceback__)
            failure = _RunFixturePublicationFailure(
                f"Run fixture {handle.name} publication failed: "
                f"{diagnostic.type_name}: {diagnostic.message}"
            )
        self._run[handle.key] = (generator, failure, handle.name)
        self._run_order.append(handle.key)
        return failure

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

    async def _await_run_setup(self, driver: AsyncFixtureDriver[bytes]) -> bytes:
        return await driver.wait_setup()

    def _load_session_sync[R](self, handle: Fixture[R]) -> R:
        slot = self._session.get(handle.key)
        if slot is not None:
            return cast("R", slot[1])
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        gen = handle.make()
        with (
            self._fixture_setup_scope(handle.key, handle.name, "session"),
            self._session_setup_scope(handle.name),
            task_scope(gen),
        ):
            try:
                value = next(gen)
            finally:
                self._session_stack.append((handle.name, gen))
        self._session[handle.key] = (gen, value, handle.name)
        return value

    def _load_session_async[R](self, handle: AsyncFixture[R]) -> Coroutine[R]:
        slot = self._session.get(handle.key)
        if slot is None:
            _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
            agen = handle.make()
            return self._create_async_session_setup(handle.key, handle.name, agen)
        cached = slot[1]
        if isinstance(cached, _PendingAsyncSessionFixtureSetup):
            return cast("Coroutine[R]", cached.driver.wait_setup())
        return self._wrap_async_session_result(cast("R", cached))

    def _create_async_session_setup[R](
        self, key: object, name: str, agen: AsyncGenerator[R]
    ) -> Coroutine[R]:
        async def setup() -> R:
            setup_completed = False
            try:
                with (
                    self._fixture_setup_scope(key, name, "session"),
                    self._session_setup_scope(name),
                ):
                    result = await anext(agen)
                setup_completed = True
            finally:
                self._session_stack.append((name, driver))
                _ = self._pending_session_tasks.pop(driver, None)
                if not setup_completed:
                    self._session.pop(key, None)
            self._session[key] = (driver, result, name)
            return result

        driver = AsyncFixtureDriver(name, agen, setup)
        self._pending_session_tasks[driver] = name
        self._session[key] = (driver, _PendingAsyncSessionFixtureSetup(driver), name)
        return cast("Coroutine[R]", driver.wait_setup())

    def _wrap_async_session_result[R](self, result: R) -> Coroutine[R]:
        async def wrapper() -> R:
            return result

        return cast("Coroutine[R]", wrapper())

    @defer_cancellation
    async def teardown_function_fixtures(
        self, *, cleanup_timeout: float | None = None
    ) -> list[TeardownFailure]:
        """Tear down active function fixtures in first-in-last-out order."""
        timeout = (
            DEFAULT_CLEANUP_TIMEOUT_SECONDS
            if cleanup_timeout is None
            else cleanup_timeout
        )
        self._function_teardown_started = True
        self._tearing_down = True
        try:
            failures = await _cancel_pending_setups(
                self._pending_function_tasks.items(),
                cleanup_timeout=timeout,
                abandoned=True,
            )
            for fixture_name, generator in reversed(self._function_stack):
                failure = await teardown_fixture(
                    fixture_name, generator, cleanup_timeout=timeout, scope="function"
                )
                if failure is not None:
                    failures.append(failure)
                task_failure = await _cleanup_fixture_tasks(
                    fixture_name,
                    generator,
                    cleanup_timeout=timeout,
                )
                failures.extend(task_failure)
            self._function_stack.clear()
            return failures
        finally:
            self._tearing_down = False

    @defer_cancellation
    async def teardown_session_fixtures(
        self, *, cleanup_timeout: float | None = None
    ) -> list[TeardownFailure]:
        """Tear down session fixtures in reverse setup order."""
        timeout = (
            DEFAULT_CLEANUP_TIMEOUT_SECONDS
            if cleanup_timeout is None
            else cleanup_timeout
        )
        self._tearing_down = True
        try:
            failures = await _cancel_pending_setups(
                self._pending_session_tasks.items(), cleanup_timeout=timeout
            )
            for name, generator in reversed(self._session_stack):
                failure = await teardown_fixture(
                    name, generator, cleanup_timeout=timeout, scope="session"
                )
                if failure is not None:
                    failures.append(failure)
                task_failure = await _cleanup_fixture_tasks(
                    name,
                    generator,
                    cleanup_timeout=timeout,
                )
                failures.extend(task_failure)
            self._session.clear()
            self._session_stack.clear()
            return failures
        finally:
            self._tearing_down = False

    @defer_cancellation
    async def teardown_run_fixtures(
        self, *, cleanup_timeout: float | None = None
    ) -> list[TeardownFailure]:
        """Tear down host-owned run fixtures in reverse dependency order."""
        timeout = (
            DEFAULT_CLEANUP_TIMEOUT_SECONDS
            if cleanup_timeout is None
            else cleanup_timeout
        )
        self._tearing_down = True
        try:
            failures = await _cancel_pending_setups(
                self._pending_run_tasks.items(), cleanup_timeout=timeout
            )
            for key in reversed(self._run_order):
                generator, cached, name = self._run[key]
                if isinstance(cached, _PendingAsyncRunFixtureSetup):
                    continue
                failure = await teardown_fixture(
                    name, generator, cleanup_timeout=timeout, scope="run"
                )
                if failure is not None:
                    failures.append(failure)
                task_failure = await _cleanup_fixture_tasks(
                    name,
                    generator,
                    cleanup_timeout=timeout,
                )
                failures.extend(task_failure)
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
