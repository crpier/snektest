import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from functools import wraps
from inspect import Parameter, Signature, currentframe, iscoroutinefunction
from typing import Any, Literal, Protocol, TypeVar, cast, overload

from hypothesis import given

from snektest.annotations import (
    AsyncFixture,
    AsyncSessionFixture,
    Coroutine,
    Fixture,
    SessionFixture,
)
from snektest.fixtures import (
    is_session_fixture,
    load_function_fixture,
    load_session_fixture,
    register_session_fixture_from_namespace,
)
from snektest.models import Param
from snektest.utils import get_code_from_generator, mark_test_function

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


def _normalize_marker_entry(entry: object) -> str:
    if entry in VALID_MARKERS:
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


def test(
    *params: list[Param[Any]],
    mark: Marker | None = None,
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None] | None]],
    Callable[[*tuple[Any, ...]], Coroutine[None] | None],
]:
    """Mark a function as a test function with an optional built-in marker."""

    markers = _normalize_markers(mark)

    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None] | None],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None] | None]:
        mark_test_function(test_func, params, markers)
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
    hypothesis_runner.__signature__ = signature  # pyright: ignore[reportFunctionMemberAccess]

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
    strategy_values: tuple[Any, ...],
    param_values: tuple[Any, ...],
) -> None:
    done: Future[None] = Future()

    def schedule() -> None:
        try:
            res = cast(
                "Coroutine[None]",
                test_func(*strategy_values, *param_values),
            )
            task: asyncio.Task[None] = loop.create_task(res)
        except Exception as exc:
            done.set_exception(exc)
            return

        def on_done(task: asyncio.Task[None]) -> None:
            try:
                task.result()
            except Exception as exc:
                done.set_exception(exc)
            else:
                done.set_result(None)

        task.add_done_callback(on_done)

    _ = loop.call_soon_threadsafe(schedule)
    done.result()


def test_hypothesis(
    *strategies: SearchStrategy[Any],
    mark: Marker | None = None,
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

    def decorator(
        test_func: Callable[..., Coroutine[None] | None],
    ) -> Callable[..., Coroutine[None] | None]:
        if iscoroutinefunction(test_func):

            @wraps(test_func)
            async def async_wrapper() -> None:
                loop = asyncio.get_running_loop()

                def run_one_example(*strategy_values: Any) -> None:
                    _run_async_example(
                        loop,
                        test_func,
                        strategy_values=tuple(strategy_values),
                        param_values=(),
                    )

                def run_hypothesis() -> None:
                    _run_hypothesis(async_wrapper, strategies_tuple, run_one_example)

                await asyncio.to_thread(run_hypothesis)

            mark_test_function(async_wrapper, (), markers)
            return async_wrapper

        @wraps(test_func)
        def sync_wrapper() -> None:
            def run_one_example(*strategy_values: Any) -> None:
                _ = test_func(*strategy_values)

            _run_hypothesis(sync_wrapper, strategies_tuple, run_one_example)

        mark_test_function(sync_wrapper, (), markers)
        return sync_wrapper

    return decorator


@overload
def load_fixture[R](
    fixture_gen: Fixture[R] | SessionFixture[R],
) -> R: ...


@overload
def load_fixture[R](
    fixture_gen: AsyncFixture[R] | AsyncSessionFixture[R],
) -> Coroutine[R]: ...


def load_fixture[R](
    fixture_gen: Fixture[R]
    | AsyncFixture[R]
    | SessionFixture[R]
    | AsyncSessionFixture[R],
) -> Coroutine[R] | R:
    """Load a fixture from a generator.
    When loading a fixture, `snektest` takes care to handle tearing down the
    fixture after the test has finished."""
    fixture_gen_code = get_code_from_generator(fixture_gen)
    frame = currentframe()
    caller_frame = frame.f_back if frame is not None else None
    if caller_frame is not None:
        register_session_fixture_from_namespace(
            fixture_gen_code, caller_frame.f_globals
        )
        register_session_fixture_from_namespace(fixture_gen_code, caller_frame.f_locals)
    del frame
    del caller_frame

    if is_session_fixture(fixture_gen_code):
        return load_session_fixture(fixture_gen)

    return load_function_fixture(fixture_gen)
