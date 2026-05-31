"""Basic snektest examples."""

from snektest import assert_eq, assert_in, test


@test(mark="fast")
def test_addition() -> None:
    """Use rich assertions in a synchronous test."""
    assert_eq(1 + 1, 2)


@test(mark="fast")
def test_string_membership() -> None:
    """Use assertion helpers instead of bare assert statements."""
    assert_in("snek", "snektest")
