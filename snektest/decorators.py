"""Public decorators and fixture-loading APIs."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from concurrent.futures import Future
from functools import wraps
from inspect import (
    Parameter,
    Signature,
    isasyncgenfunction,
    iscoroutinefunction,
    signature,
)
from typing import Any, Literal, Never, Protocol, TypeVar, cast, overload

from hypothesis import given

from snektest.annotations import AsyncFixture, Coroutine, Fixture, FixtureScope, Scope
from snektest.fixtures import current_registry, load_run_fixture
from snektest.models import (
    BadRequestError,
    Param,
    _ExpectedFailureSignal,
    _SkipSignal,
)
from snektest.utils import mark_test_function

_given = cast("Any", given)

T_co = TypeVar("T_co", covariant=True)


class SearchStrategy(Protocol[T_co]):
    def example(self) -> T_co: ...


type Marker = Literal["fast", "medium", "slow"]
"""Markers for test functions.

Markers describe the resources a test may use,
not how long it is expected to take.

`fast` means the test runs entirely in memory,
without IO, threads, or subprocesses.
`medium` means the test may use local IO or threads,
but not network IO or subprocesses.
`slow` means the test may use network IO, subprocesses,
or other expensive external resources.
"""


VALID_MARKERS: tuple[Marker, ...] = ("slow", "medium", "fast")


def _normalize_outcome_reason(reason: object) -> str:
    """Require explanations that remain useful in summaries and stored output."""
    if isinstance(reason, str) and reason and reason == reason.strip():
        return reason
    msg = "Outcome reason must be a non-empty, already-trimmed string"
    raise TypeError(msg)


def skip(reason: str) -> Never:
    """Stop the current test and report it as skipped with `reason`."""
    raise _SkipSignal(_normalize_outcome_reason(reason))


def xfail(reason: str) -> Never:
    """Stop the current test and report an expected failure with `reason`."""
    raise _ExpectedFailureSignal(_normalize_outcome_reason(reason))


type RunFixtureIdentity = tuple[str, str]
_run_fixture_catalog: dict[
    RunFixtureIdentity, Callable[[], Fixture[Any] | AsyncFixture[Any]]
] = {}


def _normalize_marker_entry(entry: object) -> str:
    if isinstance(entry, str) and entry in VALID_MARKERS:
        return entry
    msg = "Markers must be Marker literals"
    raise TypeError(msg)


def _normalize_markers(mark: object | None) -> tuple[str, ...]:
    if mark is None:
        return ()
    if mark in VALID_MARKERS:
        return (_normalize_marker_entry(mark),)
    msg = "Markers must be a single Marker literal"
    raise TypeError(msg)


def _normalize_mutex(mutex: object | None) -> str | None:
    """Validate one exact, non-empty command-local mutex name."""
    if mutex is None:
        return None
    if isinstance(mutex, str) and mutex and mutex == mutex.strip():
        return mutex
    msg = "Mutex must be a non-empty, already-trimmed string"
    raise TypeError(msg)


def test(
    *params: list[Param[Any]],
    mark: Marker | None = None,
    mutex: str | None = None,
    xfail: str | None = None,
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None] | None]],
    Callable[[*tuple[Any, ...]], Coroutine[None] | None],
]:
    """Mark a function as a test function with an optional built-in marker."""

    if len(params) == 1 and callable(params[0]):
        msg = "Bare @test is unsupported. Use @test() instead"
        raise BadRequestError(msg)

    markers = _normalize_markers(mark)
    normalized_mutex = _normalize_mutex(mutex)

    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None] | None],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None] | None]:
        mark_test_function(
            test_func,
            params,
            markers,
            normalized_mutex,
            expected_failure_reason=(
                None if xfail is None else _normalize_outcome_reason(xfail)
            ),
        )
        return test_func

    return decorator


def _maybe_apply_hypothesis_settings(
    source: Callable[..., object],
    target: Callable[..., object],
) -> Callable[..., object]:
    """Propagate `@hypothesis.settings` applied to `source` onto `target`."""

    settings_obj = getattr(source, "_hypothesis_internal_use_settings", None)
    if settings_obj is None:
        return target

    settings_decorator = cast(
        "Callable[[Callable[..., object]], Callable[..., object]]",
        settings_obj,
    )
    return settings_decorator(target)


def _run_hypothesis(
    wrapper: Callable[..., object],
    strategies: tuple[SearchStrategy[Any], ...],
    run_one_example: Callable[..., None],
) -> None:
    def hypothesis_runner(*strategy_values: Any) -> None:
        run_one_example(*strategy_values)

    signature = Signature(
        parameters=[
            Parameter(f"arg{i}", kind=Parameter.POSITIONAL_OR_KEYWORD)
            for i in range(len(strategies))
        ]
    )
    hypothesis_runner.__signature__ = signature  # ty: ignore[unresolved-attribute]

    hypothesis_runner_wrapped = _given(*strategies)(hypothesis_runner)

    runner = cast(
        "Callable[[], None]",
        _maybe_apply_hypothesis_settings(wrapper, hypothesis_runner_wrapped),
    )
    runner()


def _run_async_example(
    loop: asyncio.AbstractEventLoop,
    test_func: Callable[..., Coroutine[None] | None],
    *,
    active_tasks: set[asyncio.Task[None]],
    strategy_values: tuple[Any, ...],
    param_values: tuple[Any, ...],
) -> None:
    """Run one async example while preserving all outcomes across the thread.

    Internal control flow and task cancellation may inherit directly from
    `BaseException`. Every outcome must complete `done`; otherwise the Hypothesis
    worker blocks during interpreter executor shutdown.
    """
    done: Future[None] = Future()

    def schedule() -> None:
        try:
            res = cast(
                "Coroutine[None]",
                test_func(*strategy_values, *param_values),
            )
            task: asyncio.Task[None] = loop.create_task(res)
            active_tasks.add(task)
        except BaseException as exc:
            done.set_exception(exc)
            return

        def on_done(task: asyncio.Task[None]) -> None:
            active_tasks.discard(task)
            try:
                task.result()
            except BaseException as exc:
                done.set_exception(exc)
            else:
                done.set_result(None)

        task.add_done_callback(on_done)

    _ = loop.call_soon_threadsafe(schedule)
    done.result()


async def _run_async_hypothesis(
    wrapper: Callable[..., object],
    test_func: Callable[..., Coroutine[None] | None],
    strategies: tuple[SearchStrategy[Any], ...],
    *,
    param_values: tuple[Any, ...],
) -> None:
    """Run Hypothesis in a worker and finish its unwind before cancellation."""
    loop = asyncio.get_running_loop()
    active_tasks: set[asyncio.Task[None]] = set()
    worker_finished = asyncio.Event()

    def run_one_example(*strategy_values: Any) -> None:
        _run_async_example(
            loop,
            test_func,
            active_tasks=active_tasks,
            strategy_values=tuple(strategy_values),
            param_values=param_values,
        )

    def run_hypothesis() -> None:
        try:
            _run_hypothesis(wrapper, strategies, run_one_example)
        finally:
            _ = loop.call_soon_threadsafe(worker_finished.set)

    try:
        await asyncio.to_thread(run_hypothesis)
    except asyncio.CancelledError:
        for task in tuple(active_tasks):
            _ = task.cancel()
        if active_tasks:
            _ = await asyncio.gather(*active_tasks, return_exceptions=True)
        await worker_finished.wait()
        raise


def test_hypothesis(
    *strategies: SearchStrategy[Any],
    mark: Marker | None = None,
    mutex: str | None = None,
) -> Callable[
    [Callable[..., Coroutine[None] | None]],
    Callable[..., Coroutine[None] | None],
]:
    """Mark a function as a property-based test using Hypothesis.

    Strategies are positional and fill function arguments from left to right.
    Use `mark=` with a `Marker` literal to attach a built-in snektest marker.

    Notes:
    - Hypothesis cannot directly run async functions; for `async def` tests we run
      the Hypothesis engine in a worker thread and schedule the async test body
      back onto the main event loop.
    - Apply `@hypothesis.settings(...)` above or below this decorator to adjust
      Hypothesis behavior.
    """

    if len(strategies) == 0:
        msg = "test_hypothesis() requires at least one strategy"
        raise ValueError(msg)

    strategies_tuple = tuple(strategies)
    markers = _normalize_markers(mark)
    normalized_mutex = _normalize_mutex(mutex)

    def decorator(
        test_func: Callable[..., Coroutine[None] | None],
    ) -> Callable[..., Coroutine[None] | None]:
        if iscoroutinefunction(test_func):

            @wraps(test_func)
            async def async_wrapper() -> None:
                await _run_async_hypothesis(
                    async_wrapper,
                    test_func,
                    strategies_tuple,
                    param_values=(),
                )

            mark_test_function(async_wrapper, (), markers, normalized_mutex)
            return async_wrapper

        @wraps(test_func)
        def sync_wrapper() -> None:
            def run_one_example(*strategy_values: Any) -> None:
                _ = test_func(*strategy_values)

            _run_hypothesis(sync_wrapper, strategies_tuple, run_one_example)

        mark_test_function(sync_wrapper, (), markers, normalized_mutex)
        return sync_wrapper

    return decorator


class _FunctionDecorator(Protocol):
    @overload
    def __call__[**P, T](
        self, func: Callable[P, Generator[T]]
    ) -> Callable[P, Fixture[T]]: ...
    @overload
    def __call__[**P, T](
        self, func: Callable[P, AsyncGenerator[T]]
    ) -> Callable[P, AsyncFixture[T]]: ...


class _SessionDecorator(Protocol):
    @overload
    def __call__[T](
        self, func: Callable[[], Generator[T]]
    ) -> Callable[[], Fixture[T]]: ...
    @overload
    def __call__[T](
        self, func: Callable[[], AsyncGenerator[T]]
    ) -> Callable[[], AsyncFixture[T]]: ...


class _RunDecorator(Protocol):
    @overload
    def __call__[T](
        self, func: Callable[[], Generator[T]]
    ) -> Callable[[], Fixture[T]]: ...
    @overload
    def __call__[T](
        self, func: Callable[[], AsyncGenerator[T]]
    ) -> Callable[[], AsyncFixture[T]]: ...


def _normalize_fixture_scope(scope: object) -> FixtureScope:
    """Normalize supported literals to the public runtime scope enum."""
    if isinstance(scope, Scope):
        return scope
    if isinstance(scope, str):
        try:
            return Scope(scope)
        except ValueError:
            pass
    msg = "Fixture scope must be 'function', 'session', or 'run'"
    raise TypeError(msg)


@overload
def fixture[**P, T](func: Callable[P, Generator[T]]) -> Callable[P, Fixture[T]]: ...
@overload
def fixture[**P, T](
    func: Callable[P, AsyncGenerator[T]],
) -> Callable[P, AsyncFixture[T]]: ...
@overload
def fixture(*, scope: Literal["session", Scope.SESSION]) -> _SessionDecorator: ...
@overload
def fixture(*, scope: Literal["run", Scope.RUN]) -> _RunDecorator: ...
@overload
def fixture(
    *, scope: Literal["function", Scope.FUNCTION] = ...
) -> _FunctionDecorator: ...
def fixture(
    func: Callable[..., Generator[Any]]
    | Callable[..., AsyncGenerator[Any]]
    | None = None,
    *,
    scope: Scope | Literal["function", "run", "session"] = Scope.FUNCTION,
) -> Any:
    """Mark a generator function as a fixture.

    `@fixture` (default) is function-scoped: set up and torn down for each test.
    Session fixtures are reused within one execution process. Run fixtures are
    owned once by the run and publish bounded pickle descriptors. Session and run
    fixtures cannot take parameters.
    """

    normalized_scope = _normalize_fixture_scope(scope)

    def decorate(
        f: Callable[..., Generator[Any]] | Callable[..., AsyncGenerator[Any]],
    ) -> Callable[..., Fixture[Any] | AsyncFixture[Any]]:
        if normalized_scope == "run":
            if signature(f).parameters:
                msg = "Run fixtures cannot accept parameters"
                raise TypeError(msg)
            fixture_qualname = cast("str", getattr(f, "__qualname__", ""))
            if "." in fixture_qualname:
                msg = "Run fixtures must be defined at module scope"
                raise TypeError(msg)
        is_async = isasyncgenfunction(f)
        fixture_name = cast("str", getattr(f, "__name__", "fixture"))

        @wraps(f)
        def make_handle(*args: Any, **kwargs: Any) -> Fixture[Any] | AsyncFixture[Any]:
            if is_async:
                async_make = cast(
                    "Callable[[], AsyncGenerator[Any]]", lambda: f(*args, **kwargs)
                )
                return AsyncFixture(
                    make=async_make,
                    scope=normalized_scope,
                    key=f,
                    name=fixture_name,
                )
            sync_make = cast("Callable[[], Generator[Any]]", lambda: f(*args, **kwargs))
            return Fixture(
                make=sync_make,
                scope=normalized_scope,
                key=f,
                name=fixture_name,
            )

        if normalized_scope == "run":
            identity = (
                cast("str", getattr(f, "__module__", "")),
                cast("str", getattr(f, "__qualname__", "")),
            )
            existing = _run_fixture_catalog.get(identity)
            if existing is not None and getattr(existing, "__wrapped__", None) is not f:
                msg = f"Run fixture identity collision for {identity[0]}.{identity[1]}"
                raise TypeError(msg)
            _run_fixture_catalog[identity] = cast(
                "Callable[[], Fixture[Any] | AsyncFixture[Any]]", make_handle
            )
        return make_handle

    if func is not None:
        return decorate(func)
    return decorate


@overload
def load_fixture[R](fix: Fixture[R]) -> R: ...
@overload
def load_fixture[R](fix: AsyncFixture[R]) -> Coroutine[R]: ...
def load_fixture[R](fix: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
    """Load a fixture from its handle.

    The active run's `FixtureRegistry` sets it up, caches session fixtures, and
    tears it down after the test (function scope) or after the run (session scope).
    """
    if fix.scope == "run":
        return cast("R | Coroutine[R]", load_run_fixture(fix))
    registry = current_registry()
    if fix.scope == "session":
        return cast("R | Coroutine[R]", registry.load_session(fix))
    return cast("R | Coroutine[R]", registry.load_function(fix))


def reset_run_fixture_catalog() -> None:
    """Forget registrations from an earlier in-process collection run."""
    _run_fixture_catalog.clear()


def get_run_fixture_catalog() -> dict[
    RunFixtureIdentity, Callable[[], Fixture[Any] | AsyncFixture[Any]]
]:
    """Return run fixtures registered while selected modules were imported."""
    return dict(_run_fixture_catalog)
