"""Basic tests for `--collect-only`, `--fail-fast`, and `--durations` workflows.

Requires Python 3.15 or newer; Python 3.14 is no longer supported.

Directory discovery applies Git ignore rules to symlink names, not targets.
Explicit file selection bypasses ignores; unexpected Git filtering failures in a
worktree are collection errors.

Warning message formatting failures use safe fallback text; capture still restores
standard streams and debugger hooks.
"""

lazy import re

lazy from snektest import SnektestError, assert_eq, assert_in, assert_raises, fail, test


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
    with assert_raises(SnektestError, match="expected failure"):
        fail("expected failure")


@test(mark="fast")
def test_exception_message_match() -> None:
    """Search exception text with a string or compiled regex; msg is an override.

    Invalid regexes/bytes patterns raise BadRequestError before the body.
    Mismatches and unrenderable messages fail without matching fallback text.
    """
    pattern = re.compile(r"^invalid field: name$", re.IGNORECASE)
    with assert_raises(
        ValueError, match=pattern, msg="expected a field error"
    ) as caught:
        message = "INVALID FIELD: NAME"
        raise ValueError(message)

    # Match uses regex search; use an ordinary assertion for exact equality.
    assert_eq(str(caught.exception), "INVALID FIELD: NAME")
