"""Unit tests for Hypothesis integration."""

from typing import Any, cast

from hypothesis import strategies as st

from snektest import test
from snektest.assertions import assert_raises, assert_true
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
