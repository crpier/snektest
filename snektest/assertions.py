from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from snektest.models import AssertionFailure


class ExceptionInfo:
    """Container for exception information captured by assert_raises."""

    def __init__(self) -> None:
        self.exception: BaseException | None = None


@contextmanager
def assert_raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...],
    *,
    msg: str | None = None,
) -> Generator[ExceptionInfo, None, None]:
    """Context manager that asserts code raises expected exception.

    Args:
        expected_exception: Exception type(s) to expect
        msg: Optional custom message

    Yields:
        ExceptionInfo: Object containing the caught exception

    Raises:
        AssertionFailure: If no exception raised or wrong type

    Example:
        with assert_raises(ValueError) as exc_info:
            raise ValueError("test")
        assert_eq(str(exc_info.exception), "test")
    """
    exc_info = ExceptionInfo()
    try:
        yield exc_info
    except expected_exception as exc:
        exc_info.exception = exc
        return
    except BaseException as exc:
        # Wrong exception type
        if isinstance(expected_exception, tuple):
            expected_name = " | ".join(e.__name__ for e in expected_exception)
        else:
            expected_name = expected_exception.__name__
        actual_name = type(exc).__name__
        message = msg or f"Expected {expected_name} but got {actual_name}"
        raise AssertionFailure(message, actual=actual_name, expected=expected_name) from exc

    # No exception raised
    if isinstance(expected_exception, tuple):
        expected_name = " | ".join(e.__name__ for e in expected_exception)
    else:
        expected_name = expected_exception.__name__
    message = msg or f"Expected {expected_name} but no exception was raised"
    raise AssertionFailure(message, expected=expected_name)


def assert_eq(actual: Any, expected: Any, *, msg: str | None = None) -> None:
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


def assert_ne(actual: Any, expected: Any, *, msg: str | None = None) -> None:
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


def assert_true(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is True (identity check, not truthiness).

    Raises:
        AssertionFailure: If value is not True
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
    """Assert that value is False (identity check, not falsiness).

    Raises:
        AssertionFailure: If value is not False
    """
    if value is not False:
        message = msg or f"{value!r} is not False"
        raise AssertionFailure(
            message,
            actual=value,
            expected=False,
            operator="is",
        )


def assert_is_none(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is None.

    Raises:
        AssertionFailure: If value is not None
    """
    if value is not None:
        message = msg or f"{value!r} is not None"
        raise AssertionFailure(
            message,
            actual=value,
            expected=None,
            operator="is",
        )


def assert_is_not_none(value: Any, *, msg: str | None = None) -> None:
    """Assert that value is not None.

    Raises:
        AssertionFailure: If value is None
    """
    if value is None:
        message = msg or "value is None"
        raise AssertionFailure(
            message,
            actual=value,
            expected="not None",
            operator="is not",
        )


def assert_is(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual is expected (identity check).

    Raises:
        AssertionFailure: If actual is not expected
    """
    if actual is not expected:
        message = msg or f"{actual!r} is not {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="is",
        )


def assert_is_not(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual is not expected (identity check).

    Raises:
        AssertionFailure: If actual is expected
    """
    if actual is expected:
        message = msg or f"{actual!r} is {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="is not",
        )


def assert_lt(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual < expected.

    Raises:
        AssertionFailure: If actual >= expected
    """
    if not actual < expected:
        message = msg or f"{actual!r} >= {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="<",
        )


def assert_gt(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual > expected.

    Raises:
        AssertionFailure: If actual <= expected
    """
    if not actual > expected:
        message = msg or f"{actual!r} <= {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator=">",
        )


def assert_le(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual <= expected.

    Raises:
        AssertionFailure: If actual > expected
    """
    if not actual <= expected:
        message = msg or f"{actual!r} > {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator="<=",
        )


def assert_ge(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    """Assert that actual >= expected.

    Raises:
        AssertionFailure: If actual < expected
    """
    if not actual >= expected:
        message = msg or f"{actual!r} < {expected!r}"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
            operator=">=",
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


def assert_not_in(member: Any, container: Any, *, msg: str | None = None) -> None:
    """Assert that member not in container.

    Raises:
        AssertionFailure: If member in container
    """
    if member in container:
        message = msg or f"{member!r} found in {container!r}"
        raise AssertionFailure(
            message,
            actual=member,
            expected=container,
            operator="not in",
        )


def assert_isinstance(
    obj: Any, classinfo: type | tuple[type, ...], *, msg: str | None = None
) -> None:
    """Assert that isinstance(obj, classinfo) is True.

    Raises:
        AssertionFailure: If isinstance(obj, classinfo) is False
    """
    if not isinstance(obj, classinfo):
        type_name = (
            classinfo.__name__ if isinstance(classinfo, type) else str(classinfo)
        )
        message = msg or f"{obj!r} is not an instance of {type_name}"
        raise AssertionFailure(
            message,
            actual=type(obj).__name__,
            expected=type_name,
            operator="isinstance",
        )


def assert_not_isinstance(
    obj: Any, classinfo: type | tuple[type, ...], *, msg: str | None = None
) -> None:
    """Assert that isinstance(obj, classinfo) is False.

    Raises:
        AssertionFailure: If isinstance(obj, classinfo) is True
    """
    if isinstance(obj, classinfo):
        type_name = (
            classinfo.__name__ if isinstance(classinfo, type) else str(classinfo)
        )
        message = msg or f"{obj!r} is an instance of {type_name}"
        raise AssertionFailure(
            message,
            actual=type(obj).__name__,
            expected=f"not {type_name}",
            operator="not isinstance",
        )


def assert_len(obj: Any, expected_length: int, *, msg: str | None = None) -> None:
    """Assert that len(obj) == expected_length.

    Raises:
        AssertionFailure: If len(obj) != expected_length
    """
    actual_length = len(obj)
    if actual_length != expected_length:
        message = msg or f"Length {actual_length} != {expected_length}"
        raise AssertionFailure(
            message,
            actual=actual_length,
            expected=expected_length,
            operator="len ==",
        )


def fail(msg: str | None = None) -> None:
    """Unconditionally raise an AssertionFailure.

    Args:
        msg: Optional custom message

    Raises:
        AssertionFailure: Always raises
    """
    message = msg or "Assertion failed"
    raise AssertionFailure(message)
