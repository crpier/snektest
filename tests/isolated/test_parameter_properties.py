"""Properties for parameter expansion and case-identifier round trips."""

from __future__ import annotations

from math import prod
from pathlib import Path
from string import ascii_letters, digits

from hypothesis import settings
from hypothesis import strategies as st

from snektest import Param, assert_eq, assert_isinstance, assert_raises, test
from snektest.decorators import test_hypothesis
from snektest.models import BadRequestError, FilterItem

_axis_sizes = st.lists(st.integers(min_value=1, max_value=5), min_size=1, max_size=4)
_safe_case_names = st.text(
    alphabet=ascii_letters + digits + " -_",
    min_size=1,
    max_size=20,
)


@settings(deadline=None)
@test_hypothesis(_axis_sizes, mark="fast")
def test_parameter_expansion_has_cartesian_cardinality(axis_sizes: list[int]) -> None:
    """Expansion produces exactly the product of all declared axis sizes."""
    axes = tuple(
        [
            Param(value=(axis_index, value_index), name=f"{axis_index}-{value_index}")
            for value_index in range(axis_size)
        ]
        for axis_index, axis_size in enumerate(axis_sizes)
    )

    combinations = Param.to_dict(axes)

    assert_eq(len(combinations), prod(axis_sizes))


@test(mark="fast")
def test_parameter_expansion_rejects_more_than_ten_thousand_cases() -> None:
    """An unexpectedly large Cartesian product fails before allocation."""

    axes = tuple(
        [Param(value=index, name=str(index)) for index in range(axis_size)]
        for axis_size in (101, 100)
    )

    with assert_raises(BadRequestError) as raised:
        _ = Param.to_dict(axes)

    assert_eq(
        str(raised.exception),
        "Parameterized test expands to 10100 cases; maximum is 10000",
    )


@settings(deadline=None)
@test_hypothesis(_safe_case_names, mark="fast")
def test_parameter_identifier_round_trips_through_filter(case_name: str) -> None:
    """Every accepted generated identifier survives rendering and parsing."""
    combinations = Param.to_dict(([Param(value=1, name=case_name)],))
    rendered_name = next(iter(combinations))

    selected = assert_isinstance(
        FilterItem(f"{Path(__file__)}::test_case[{rendered_name}]"), FilterItem
    )

    assert_eq(selected.params, case_name)
