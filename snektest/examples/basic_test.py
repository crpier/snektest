"""Basic tests for `--collect-only`, `--fail-fast`, and `--durations` workflows.

Requires Python 3.15 or newer; Python 3.14 is no longer supported.

Pass observed values first and expected values second in comparisons. Bind the
return values of narrowing helpers; use `_ =` when only checking a value's type.
For exception text, `match=` is regex search, capture plus assert_eq is exact,
and `msg=` is only the failure diagnostic. Inspect original causes explicitly.
Print the usage guide with `snektest --agent-docs`.

Directory discovery applies Git ignore rules to symlink names, not targets.
Explicit file selection bypasses ignores; unexpected Git filtering failures in a
worktree are collection errors.

Warning message formatting failures use safe fallback text; capture still restores
standard streams and debugger hooks.
"""

lazy import re

lazy from snektest import (
    AssertionFailure,
    SnektestError,
    assert_eq,
    assert_in,
    assert_is_not_none,
    assert_isinstance,
    assert_raises,
    fail,
    test,
)


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


@test(mark="fast")
def test_structured_comparison_diagnostic() -> None:
    """A captured failure identifies the nested path, not just two large reprs."""
    with assert_raises(AssertionFailure) as mismatch:
        assert_eq({"types": ["int", "str"]}, {"types": ["int", "bytes"]})
    assert_in("At $['types'][1]: 'str' != 'bytes'", str(mismatch.exception))


def _parse_port(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        message = "invalid port"
        raise RuntimeError(message) from error


@test(mark="fast")
def test_exception_cause() -> None:
    with assert_raises(RuntimeError, msg="port conversion should fail") as caught:
        _ = _parse_port("not-a-number")

    assert_eq(str(caught.exception), "invalid port")
    cause = assert_isinstance(caught.exception.__cause__, ValueError)
    assert_in("not-a-number", str(cause))


@test(mark="fast")
def test_bound_narrowing_helpers() -> None:
    settings: dict[str, object] = {"suite": "SNEKTEST"}
    configured = assert_is_not_none(settings.get("suite"))
    name = assert_isinstance(configured, str)
    assert_eq(name.lower(), "snektest")
