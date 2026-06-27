"""Fixture registry: per-run ownership of caching, setup, and teardown."""

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from inspect import isasyncgen, isgenerator, signature
from types import TracebackType
from typing import Any, cast

from snektest.annotations import AsyncFixture, Coroutine, Fixture
from snektest.models import (
    BadRequestError,
    FixtureError,
    TeardownFailure,
    UnreachableError,
)

type _SessionSlot = tuple[AsyncGenerator[Any] | Generator[Any] | None, object, str]


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

    awaitable: Awaitable[Any]


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
            fixture_name=fixture_name,
            exc_type=cast("type[BaseException]", exc_type),
            exc_value=cast("BaseException", exc_value),
            traceback=traceback,
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
        self._function_stack: list[
            tuple[str, AsyncGenerator[Any] | Generator[Any]]
        ] = []

    def load_function[R](
        self, handle: Fixture[R] | AsyncFixture[R]
    ) -> R | Coroutine[R]:
        """Set up a function-scoped fixture and register it for teardown."""
        if isinstance(handle, AsyncFixture):
            agen = handle.make()
            self._function_stack.append((handle.name, agen))
            return cast("Coroutine[R]", agen.__anext__())
        gen = handle.make()
        self._function_stack.append((handle.name, gen))
        return next(gen)

    def load_session[R](self, handle: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
        """Set up a session-scoped fixture once and reuse it thereafter."""
        if isinstance(handle, AsyncFixture):
            return self._load_session_async(handle)
        return self._load_session_sync(handle)

    def _load_session_sync[R](self, handle: Fixture[R]) -> R:
        slot = self._session.get(handle.key)
        if slot is not None:
            return cast("R", slot[1])
        _ensure_session_fixture_has_no_parameters(handle.key, handle.name)
        gen = handle.make()
        value = next(gen)
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
            return cast("Coroutine[R]", cached.awaitable)
        return self._wrap_async_session_result(cast("R", cached))

    def _create_async_session_setup[R](
        self, key: object, name: str, agen: AsyncGenerator[R]
    ) -> Coroutine[R]:
        async def result_updater() -> R:
            registered_gen = self._session[key][0]
            if not isasyncgen(registered_gen):
                msg = "Async session fixture setup lost its generator. This shouldn't be possible!"
                raise UnreachableError(msg)
            result = cast("R", await anext(registered_gen))
            self._session[key] = (registered_gen, result, name)
            return result

        awaitable: Awaitable[R]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            awaitable = result_updater()
        else:
            awaitable = loop.create_task(result_updater())
        self._session[key] = (agen, _PendingAsyncSessionFixtureSetup(awaitable), name)
        return cast("Coroutine[R]", awaitable)

    def _wrap_async_session_result[R](self, result: R) -> Coroutine[R]:
        async def wrapper() -> R:
            return result

        return wrapper()

    async def teardown_function_fixtures(self) -> list[TeardownFailure]:
        """Tear down active function fixtures in first-in-last-out order."""
        failures: list[TeardownFailure] = []
        for fixture_name, generator in reversed(self._function_stack):
            failure = await teardown_fixture(fixture_name, generator)
            if failure is not None:
                failures.append(failure)
        self._function_stack.clear()
        return failures

    async def teardown_session_fixtures(self) -> list[TeardownFailure]:
        """Tear down session fixtures in reverse setup order."""
        failures: list[TeardownFailure] = []
        for generator, _result, name in reversed(list(self._session.values())):
            if generator is not None:
                failure = await teardown_fixture(name, generator)
                if failure is not None:
                    failures.append(failure)
        return failures


_current_registry: ContextVar[FixtureRegistry] = ContextVar("snektest_fixture_registry")


def current_registry() -> FixtureRegistry:
    """Return the fixture registry for the current run."""
    try:
        return _current_registry.get()
    except LookupError:
        msg = "No active fixture registry. `load_fixture` must be called during a snektest run."
        raise UnreachableError(msg) from None


@contextmanager
def use_registry(registry: FixtureRegistry) -> Generator[FixtureRegistry]:
    """Bind a fixture registry for the duration of a run."""
    token = _current_registry.set(registry)
    try:
        yield registry
    finally:
        _current_registry.reset(token)
