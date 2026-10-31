"""Keep an async generator's entire fixture lifetime in its owning task."""

lazy import warnings
lazy from collections.abc import AsyncGenerator, Callable, Coroutine
lazy from contextlib import suppress
lazy from dataclasses import dataclass
lazy from inspect import AGEN_SUSPENDED, getasyncgenstate

lazy from snektest.models import (
    DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    BadRequestError,
    FixtureError,
    FixtureTeardownTimeoutError,
)
lazy from snektest.output import fixture_warning_capture, relay_fixture_warnings
lazy from snektest.runtime import cancelled_errors, current_backend
lazy from snektest.runtime import timeout as runtime_timeout
lazy from snektest.runtime_tasks import (
    ResultCell,
    Task,
    TrioTask,
    event,
    shield,
    spawn,
    wait,
)
lazy from snektest.task_cleanup import cancel_tasks, task_scope


@dataclass(frozen=True)
class _Value[T]:
    value: T


@dataclass(frozen=True)
class _Failure:
    error: BaseException


class AsyncFixtureDriver[T]:
    """Deliver setup while keeping task-local scopes alive until teardown.

    Exceptions cross the setup seam as values so SystemExit/KeyboardInterrupt
    cannot terminate the event loop inside the fixture's child task.
    """

    def __init__(
        self,
        name: str,
        generator: AsyncGenerator[T],
        setup: Callable[[], Coroutine[object, object, T]],
    ) -> None:
        self.name = name
        self.generator = generator
        self._ready = ResultCell[_Value[T] | _Failure]()
        self._warnings: list[warnings.WarningMessage] = []
        self._release = event()
        self._timeout = DEFAULT_CLEANUP_TIMEOUT_SECONDS
        self._yielded = False
        self._setup_observed = False
        self._cleanup_cancelled = False
        with task_scope(generator):
            self.task = spawn(
                self._run(setup), name=f"snektest fixture {name}", eager_start=False
            )
        self.task.add_done_callback(self._consume_error)

    def _consume_error(self, task: Task[BaseException | None]) -> None:
        if not self._ready.done():
            _ = self._ready.cancel()
        if not task.cancelled():
            _ = task.exception()

    async def _run(
        self, setup: Callable[[], Coroutine[object, object, T]]
    ) -> BaseException | None:
        with fixture_warning_capture() as self._warnings:
            return await self._lifetime(setup)

    async def _lifetime(
        self, setup: Callable[[], Coroutine[object, object, T]]
    ) -> BaseException | None:
        try:
            value = await setup()
        except BaseException as error:
            if isinstance(error, (*cancelled_errors(), GeneratorExit)):
                _ = self._ready.cancel()
            else:
                self._ready.set_result(_Failure(error))
            if isinstance(error, GeneratorExit):
                raise
            if getasyncgenstate(self.generator) != AGEN_SUSPENDED:
                if isinstance(error, (SystemExit, KeyboardInterrupt)):
                    return error
                raise
            # Descriptor validation can fail after the generator yielded. It
            # still needs teardown in this task even though setup was rejected.
        else:
            self._ready.set_result(_Value(value))
        self._yielded = True
        try:
            try:
                await self._release.wait()
            except cancelled_errors() as error:
                # A fixture's own task group/scope can interrupt its suspended
                # yield. Unwind it in this same task, not at loop shutdown.
                with suppress(StopAsyncIteration):
                    _ = await self.generator.athrow(error)
                msg = f"Async fixture {self.name} stopped before teardown"
                raise FixtureError(msg) from error
            await self._advance()
        except (SystemExit, KeyboardInterrupt) as error:
            return error
        return None

    async def _advance(self) -> None:
        if isinstance(self.task, TrioTask):
            # The task's OUTER scope owns this deadline. Inserting a new scope
            # beneath a generator's suspended user scope corrupts Trio's LIFO.
            try:
                await self._advance_generator()
            except cancelled_errors():
                if self.task.deadline_expired():
                    raise FixtureTeardownTimeoutError(
                        self.name, self._timeout
                    ) from None
                raise
            return
        limit = runtime_timeout(self._timeout)
        try:
            async with limit:
                await self._advance_generator()
        except TimeoutError:
            if limit.expired():
                raise FixtureTeardownTimeoutError(self.name, self._timeout) from None
            raise

    async def _advance_generator(self) -> None:
        try:
            _ = await anext(self.generator)
        except StopAsyncIteration:
            return
        msg = f"Incorrect fixture function {self.name} yielded more than once"
        error = BadRequestError(msg)
        try:
            with suppress(StopAsyncIteration):
                _ = await self.generator.athrow(error)
        finally:
            with suppress(GeneratorExit):
                await self.generator.aclose()
        raise error

    async def wait_setup(self, *, cancel_with_waiter: bool = False) -> T:
        try:
            outcome = await shield(self._ready)
        except cancelled_errors():
            if self._ready.cancelled():
                self._setup_observed = True
            if cancel_with_waiter:
                _ = self.task.cancel()
            raise
        finally:
            self.flush_warnings()
        self._setup_observed = True
        if isinstance(outcome, _Failure):
            raise outcome.error
        return outcome.value

    def flush_warnings(self) -> None:
        relay_fixture_warnings(self._warnings)

    def cancel_setup(self) -> None:
        """Let registry cleanup account for this unfinished setup exactly once."""
        self._cleanup_cancelled = True
        _ = self.task.cancel()

    async def teardown(self, cleanup_timeout: float) -> None:
        if not self._yielded and (
            self._setup_observed or self._cleanup_cancelled or self.task.cancelled()
        ):
            return
        self._timeout = cleanup_timeout
        if isinstance(self.task, TrioTask) and not self.task.done():
            self.task.set_deadline(cleanup_timeout)
        self._release.set()
        try:
            if current_backend() == "trio":
                _, pending = await wait({self.task}, timeout_seconds=cleanup_timeout)
                if pending:
                    _ = self.task.cancel()
                    _ = await cancel_tasks({self.task}, timeout=cleanup_timeout)
                    raise FixtureTeardownTimeoutError(self.name, cleanup_timeout)
            error = await shield(self.task)
            if error is not None:
                raise error
        finally:
            self.flush_warnings()
