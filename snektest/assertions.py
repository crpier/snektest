from typing import Any

from snektest.models import AssertionFailure


def assert_equal(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual == expected.

    Raises:
        AssertionFailure: If actual != expected
    """
    if actual != expected:
        message = msg or f"{actual!r} != {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="==",
        )


# Backward compatibility alias
assert_eq = assert_equal


def assert_not_equal(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual != expected.

    Raises:
        AssertionFailure: If actual == expected
    """
    if actual == expected:
        message = msg or f"{actual!r} == {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="!=",
        )


# TODO: should assert that it is `True`, right? And have another function for "is truthy"?
def assert_true(value: Any, *, msg: str | None = None) -> None:
    """Assert that bool(value) is True.

    Raises:
        AssertionFailure: If bool(value) is False
    """
    if value is not True:
        message = msg or f"{value!r} is not True"
        raise AssertionFailure(
            message,
            actual=value,
            expected=True,
            operator="is",
        )


def assert_false(value: Any, *, msg: str | None = None) -> None:
    """Assert that bool(value) is False.

    Raises:
        AssertionFailure: If bool(value) is True
    """
    if value is not False:
        message = msg or f"{value!r} is not False"
        raise AssertionFailure(
            message,
            actual=value,
            expected=False,
            operator="is",
        )


def assert_in(member: Any, container: Any, *, msg: str | None = None) -> None:
    """Assert that member in container.

    Raises:
        AssertionFailure: If member not in container
    """
    if member not in container:
        message = msg or f"{member!r} not found in {container!r}"
        raise AssertionFailure(
            message,
            actual=member,
            expected=container,
            operator="in",
        )


def assert_raise() -> None:
    # TODO: message
    msg = "Placeholder message"
    raise AssertionFailure(
        msg,
    )


# assert_true
# assert_false
# assert_is_none
# assert_is_not_none
# assert_is
# assert_is_not
# assert_eq
# assert_ne
# assert_lt
# assert_gt
# assert_le
# assert_ge
# assert_in
# assert_not_in
# assert_isinstance
# assert_not_isinstance
# assert_len
# assert_raise <- this just raises an exception, and optionally accepts a message
