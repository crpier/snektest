"""Basic tests for `--collect-only`, `--fail-fast`, and `--durations` workflows.

Directory discovery applies Git ignore rules to symlink names, not targets.
Explicit file selection bypasses ignores; unexpected Git filtering failures in a
worktree are collection errors.
"""

from snektest import SnektestError, assert_eq, assert_in, assert_raises, fail, test


@test(mark="fast")
def test_addition() -> None:
    """Use rich assertions in a synchronous test."""
    assert_eq(1 + 1, 2)


@test(mark="fast")
def test_string_membership() -> None:
    """Use assertion helpers instead of bare assert statements."""
    assert_in("snek", "snektest")


@test(mark="fast")
def test_public_error_base() -> None:
    """Catch user-facing framework failures under one conventional base."""
    with assert_raises(SnektestError):
        fail("expected failure")
