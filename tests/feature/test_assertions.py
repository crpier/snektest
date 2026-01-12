from snektest import (
    assert_eq,
    assert_false,
    assert_in,
    assert_ne,
    assert_raises,
    assert_true,
    fail,
    test,
)
from snektest.models import AssertionFailure


@test()
async def test_assert_equal_passes() -> None:
    assert_eq(5, 5)
    assert_eq("hello", "hello")
    assert_eq([1, 2, 3], [1, 2, 3])


@test()
async def test_assert_equal_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(5, 10)

    assert_eq(exc_info.exception.actual, 5)
    assert_eq(exc_info.exception.expected, 10)
    assert_eq(exc_info.exception.operator, "==")


@test()
async def test_assert_equal_custom_message() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(1, 2, msg="Custom error message")

    assert_eq(str(exc_info.exception), "Custom error message")


@test()
async def test_assert_not_equal_passes() -> None:
    assert_ne(5, 10)
    assert_ne("hello", "world")
    assert_ne([1, 2], [3, 4])


@test()
async def test_assert_not_equal_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_ne(5, 5)

    assert_eq(exc_info.exception.actual, 5)
    assert_eq(exc_info.exception.expected, 5)
    assert_eq(exc_info.exception.operator, "!=")


@test()
async def test_assert_not_equal_custom_message() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_ne("same", "same", msg="Should be different")

    assert_eq(str(exc_info.exception), "Should be different")


@test()
async def test_assert_true_passes() -> None:
    assert_true(True)


@test()
async def test_assert_true_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_true(False)

    assert_false(exc_info.exception.actual)
    assert_true(exc_info.exception.expected)
    assert_eq(exc_info.exception.operator, "is")


@test()
async def test_assert_true_fails_with_falsy_value() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_true(0)

    assert_eq(exc_info.exception.actual, 0)
    assert_true(exc_info.exception.expected)


@test()
async def test_assert_true_custom_message() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_true([], msg="List should not be empty")

    assert_eq(str(exc_info.exception), "List should not be empty")


@test()
async def test_assert_false_passes() -> None:
    assert_false(False)


@test()
async def test_assert_false_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_false(True)

    assert_true(exc_info.exception.actual)
    assert_false(exc_info.exception.expected)
    assert_eq(exc_info.exception.operator, "is")


@test()
async def test_assert_false_fails_with_truthy_value() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_false(1)

    assert_eq(exc_info.exception.actual, 1)
    assert_false(exc_info.exception.expected)


@test()
async def test_assert_false_custom_message() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_false("non-empty", msg="String should be empty")

    assert_eq(str(exc_info.exception), "String should be empty")


@test()
async def test_assert_in_passes() -> None:
    assert_in(1, [1, 2, 3])
    assert_in("h", "hello")
    assert_in("key", {"key": "value"})
    assert_in(2, {1, 2, 3})


@test()
async def test_assert_in_fails() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_in(5, [1, 2, 3])

    assert_eq(exc_info.exception.actual, 5)
    assert_eq(exc_info.exception.expected, [1, 2, 3])
    assert_eq(exc_info.exception.operator, "in")


@test()
async def test_assert_in_fails_string() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_in("z", "hello")

    assert_eq(exc_info.exception.actual, "z")
    assert_eq(exc_info.exception.expected, "hello")


@test()
async def test_assert_in_custom_message() -> None:
    with assert_raises(AssertionFailure) as exc_info:
        assert_in("missing", ["a", "b", "c"], msg="Item not in list")

    assert_eq(str(exc_info.exception), "Item not in list")


@test()
def test_assert_raises_catches_exception() -> None:
    """Test that assert_raises catches expected exception."""
    with assert_raises(ValueError) as exc_info:
        msg = "test error"
        raise ValueError(msg)

    assert_eq(type(exc_info.exception), ValueError)
    assert_eq(str(exc_info.exception), "test error")


@test()
def test_assert_raises_catches_assertion_failure() -> None:
    """Test that assert_raises works with AssertionFailure."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(5, 10)

    assert_eq(exc_info.exception.actual, 5)
    assert_eq(exc_info.exception.expected, 10)
    assert_eq(exc_info.exception.operator, "==")


@test()
def test_assert_raises_fails_when_no_exception() -> None:
    """Test that assert_raises fails if no exception is raised."""
    with assert_raises(AssertionFailure) as exc_info, assert_raises(ValueError):
        pass

    assert_eq(
        str(exc_info.exception),
        "Expected to raise an exception but no exception was raised",
    )


@test()
def test_assert_raises_fails_on_wrong_exception_type() -> None:
    """Test that assert_raises fails if wrong exception type is raised."""
    with assert_raises(AssertionFailure) as exc_info:  # noqa: SIM117
        with assert_raises(ValueError):
            msg = "wrong type"
            raise TypeError(msg)

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "Expected to raise ValueError but raised TypeError")


@test()
def test_assert_raises_custom_message() -> None:
    """Test assert_raises with custom message."""
    with assert_raises(AssertionFailure) as exc_info:  # noqa: SIM117
        with assert_raises(ValueError, msg="Custom error message"):
            pass

    assert_eq(str(exc_info.exception), "Custom error message")


@test()
def test_assert_raises_tuple_of_exceptions() -> None:
    """Test assert_raises with tuple of exception types."""
    with assert_raises(ValueError, TypeError) as exc_info:
        msg = "value error"
        raise ValueError(msg)

    assert_eq(type(exc_info.exception), ValueError)

    with assert_raises(ValueError, TypeError) as exc_info:
        msg = "type error"
        raise TypeError(msg)

    assert_eq(type(exc_info.exception), TypeError)


@test()
def test_assert_raises_tuple_fails_on_wrong_type() -> None:
    """Test that assert_raises with tuple fails on exception not in tuple."""
    with assert_raises(AssertionFailure) as exc_info:  # noqa: SIM117
        with assert_raises(ValueError, TypeError):
            msg = "wrong type"
            raise KeyError(msg)

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "Expected to raise ValueError | TypeError but raised KeyError")


@test()
def test_fail_always_raises() -> None:
    """Test that fail always raises AssertionFailure."""
    with assert_raises(AssertionFailure):
        fail()


@test()
def test_fail_with_message() -> None:
    """Test fail with custom message."""
    with assert_raises(AssertionFailure) as exc_info:
        fail("Custom failure message")

    assert_eq(str(exc_info.exception), "Custom failure message")
