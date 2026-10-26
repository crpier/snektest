"""Public exception-message matching behavior."""

import re
from typing import Any, cast, override

from snektest import (
    AssertionFailure,
    BadRequestError,
    Param,
    assert_eq,
    assert_false,
    assert_in,
    assert_is,
    assert_isinstance,
    assert_raises,
    test,
)


@test(mark="fast")
def test_match_searches_exception_text_and_preserves_identity() -> None:
    original = ValueError("prefix: invalid field: name; suffix")

    with assert_raises(ValueError, match=r"invalid field: \w+") as caught:
        raise original

    assert_is(caught.exception, original)


@test(mark="fast")
def test_invalid_regex_is_rejected_before_body() -> None:
    body_ran = False
    with (
        assert_raises(BadRequestError) as caught,
        assert_raises(ValueError, match="["),
    ):
        body_ran = True

    assert_false(body_ran)
    assert_in("valid string regular expression", str(caught.exception))
    _ = assert_isinstance(caught.exception.__cause__, re.PatternError)


class _BrokenMessageError(ValueError):
    @override
    def __str__(self) -> str:
        msg = "broken formatter"
        raise RuntimeError(msg)


@test(mark="fast")
def test_unrenderable_message_fails_without_matching_fallback_text() -> None:
    original = _BrokenMessageError("original exception")
    caught = assert_raises(ValueError, match="")
    with assert_raises(AssertionFailure) as failure, caught:
        raise original

    assert_is(caught.exception, original)
    assert_is(failure.exception.__cause__, original)
    assert_in("str() raised RuntimeError", str(failure.exception))


@test(
    [
        Param(value=b"message", name="bytes"),
        Param(value=re.compile(b"message"), name="compiled-bytes"),
        Param(value=42, name="non-pattern"),
    ],
    mark="fast",
)
def test_non_string_patterns_are_rejected_before_body(bad_match: object) -> None:
    body_ran = False
    with (
        assert_raises(BadRequestError) as caught,
        assert_raises(ValueError, match=cast("Any", bad_match)),
    ):
        body_ran = True
        msg = "message"
        raise ValueError(msg)

    assert_false(body_ran)
    assert_in("string regular expression", str(caught.exception))


@test(
    [
        Param(value="", name="empty"),
        Param(value="(?m)^invalid field: name$", name="inline-multiline"),
        Param(
            value=re.compile("^invalid field: name$", re.MULTILINE),
            name="compiled-multiline",
        ),
        Param(
            value=re.compile("INVALID FIELD", re.IGNORECASE), name="compiled-ignorecase"
        ),
    ],
    mark="fast",
)
def test_match_accepts_strings_and_preserves_compiled_flags(
    pattern: str | re.Pattern[str],
) -> None:
    original = ValueError("prefix\ninvalid field: name\nsuffix")
    with assert_raises(ValueError, match=pattern) as caught:
        raise original

    assert_is(caught.exception, original)


@test(
    [
        Param(value=ValueError("message"), name="first-type"),
        Param(value=TypeError("message"), name="second-type"),
        Param(value=UnicodeError("message"), name="subclass"),
    ],
    mark="fast",
)
def test_match_accepts_multiple_exception_types_and_subclasses(
    original: Exception,
) -> None:
    with assert_raises(ValueError, TypeError, match="message") as caught:
        raise original

    assert_is(caught.exception, original)


@test(
    [
        Param(value=None, name="default-message"),
        Param(value="custom failure", name="custom-message"),
    ],
    mark="fast",
)
def test_message_mismatch_is_an_assertion_with_original_cause(
    message: str | None,
) -> None:
    original = ValueError("actual message")
    caught = assert_raises(ValueError, match="wanted", msg=message)
    with assert_raises(AssertionFailure) as failure, caught:
        raise original

    assert_is(caught.exception, original)
    assert_is(failure.exception.__cause__, original)
    assert_eq(failure.exception.actual, "actual message")
    assert_eq(failure.exception.expected, "wanted")
    assert_eq(
        str(failure.exception),
        message or "Exception message 'actual message' does not match 'wanted'",
    )


@test(
    [
        Param(
            value=(None, "Expected to raise an exception but no exception was raised"),
            name="no-exception",
        ),
        Param(
            value=(
                TypeError("matching text"),
                "Expected to raise ValueError but raised TypeError",
            ),
            name="wrong-type",
        ),
    ],
    [
        Param(value=None, name="default-message"),
        Param(value="custom failure", name="custom-message"),
    ],
    mark="fast",
)
def test_match_preserves_missing_and_wrong_type_failures(
    case: tuple[Exception | None, str],
    message: str | None,
) -> None:
    original, expected_message = case
    with (
        assert_raises(AssertionFailure) as failure,
        assert_raises(ValueError, match="matching text", msg=message),
    ):
        if original is not None:
            raise original

    assert_eq(str(failure.exception), message or expected_message)
    assert_is(failure.exception.actual, original)
    assert_eq(failure.exception.expected, (ValueError,))


@test(mark="fast")
def test_msg_is_not_a_pattern_and_does_not_override_a_match() -> None:
    original = ValueError("actual message")
    with assert_raises(ValueError, msg="not the exception message") as without_match:
        raise original
    with assert_raises(ValueError, match="actual", msg="not a regex [") as matched:
        raise original

    assert_is(without_match.exception, original)
    assert_is(matched.exception, original)


@test(mark="fast")
def test_broken_formatter_is_not_called_without_match() -> None:
    original = _BrokenMessageError("original exception")
    with assert_raises(ValueError) as caught:
        raise original

    assert_is(caught.exception, original)
