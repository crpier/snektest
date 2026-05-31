"""Parameterized snektest examples."""

from snektest import Param, assert_eq, test


@test(
    [
        Param(value="hello", name="lowercase"),
        Param(value="WORLD", name="uppercase"),
        Param(value="MiXeD", name="mixed"),
    ],
    mark="fast",
)
def test_string_length(value: str) -> None:
    """Each Param creates one test case with a readable name."""
    assert_eq(len(value), 5)


@test(
    [Param(value="hello", name="hello"), Param(value="hi", name="hi")],
    [Param(value=" world", name="world"), Param(value=" there", name="there")],
    mark="fast",
)
def test_cartesian_product(greeting: str, target: str) -> None:
    """Multiple parameter lists are combined as a cartesian product."""
    combined = greeting + target
    assert_eq(combined[: len(greeting)], greeting)
