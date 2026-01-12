"""Unit tests for Hypothesis integration."""

import inspect
from typing import Any, cast

from hypothesis import strategies as st

from snektest import test
from snektest.assertions import assert_in, assert_raises, assert_true
from snektest.decorators import test_hypothesis
from snektest.utils import is_test_function


@test()
def test_test_hypothesis_marks_function() -> None:
    @test_hypothesis(st.integers())
    def prop(x: int) -> None:
        _ = x

    assert_true(is_test_function(prop))


@test()
def test_test_hypothesis_requires_strategies() -> None:
    with assert_raises(ValueError):
        _ = cast("Any", test_hypothesis)()


@test()
def test_test_hypothesis_without_settings_hits_default_settings_path() -> None:
    wrapper = test_hypothesis(st.just(0))

    @wrapper
    def prop(x: int) -> None:
        assert_true(x == 0)

    _ = prop()


@test()
async def test_test_hypothesis_async_schedule_and_on_done_errors() -> None:
    wrapper = test_hypothesis(st.just(0))

    def fake_coroutine_function(*args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        return None

    fake_marked = inspect.markcoroutinefunction(fake_coroutine_function)
    run_bad = wrapper(fake_marked)

    res = run_bad()
    assert res is not None
    with assert_raises(Exception) as exc_info:
        await res

    assert_in("coroutine", str(exc_info.exception).lower())

    @wrapper
    async def run_raises(_x: int) -> None:
        raise RuntimeError("boom")

    res2 = run_raises()
    assert res2 is not None
    with assert_raises(Exception):
        await res2
