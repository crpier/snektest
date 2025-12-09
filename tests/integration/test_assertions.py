from snektest import (
    assert_equal,
    assert_false,
    assert_in,
    assert_not_equal,
    assert_true,
    test,
)
from snektest.assertions import assert_raise
from snektest.models import AssertionFailure


# Test assert_equal
@test()
async def test_assert_equal_passes() -> None:
    assert_equal(5, 5)
    assert_equal("hello", "hello")
    assert_equal([1, 2, 3], [1, 2, 3])


@test()
async def test_assert_equal_fails() -> None:
    try:
        assert_equal(5, 10)
    except AssertionFailure as exc:
        assert_equal(exc.actual, 5)
        assert_equal(exc.expected, 10)
        assert_equal(exc.operator, "==")
    else:
        assert False, "Should have raised AssertionFailure"  # noqa: B011


@test()
async def test_assert_equal_custom_message() -> None:
    try:
        assert_equal(1, 2, msg="Custom error message")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(str(exc), "Custom error message")


# Test assert_not_equal
@test()
async def test_assert_not_equal_passes() -> None:
    assert_not_equal(5, 10)
    assert_not_equal("hello", "world")
    assert_not_equal([1, 2], [3, 4])


@test()
async def test_assert_not_equal_fails() -> None:
    try:
        assert_not_equal(5, 5)
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(exc.actual, 5)
        assert_equal(exc.expected, 5)
        assert_equal(exc.operator, "!=")


@test()
async def test_assert_not_equal_custom_message() -> None:
    try:
        assert_not_equal("same", "same", msg="Should be different")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(str(exc), "Should be different")


# Test assert_true
@test()
async def test_assert_true_passes() -> None:
    assert_true(True)  # noqa: FBT003
    assert_true(1)
    assert_true("non-empty string")
    assert_true([1, 2, 3])


@test()
async def test_assert_true_fails() -> None:
    try:
        assert_true(False)  # noqa: FBT003
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_false(exc.actual)
        assert_true(exc.expected)
        assert_equal(exc.operator, "is")


@test()
async def test_assert_true_fails_with_falsy_value() -> None:
    try:
        assert_true(0)
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(exc.actual, 0)
        assert_true(exc.expected)


@test()
async def test_assert_true_custom_message() -> None:
    try:
        assert_true([], msg="List should not be empty")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(str(exc), "List should not be empty")


# Test assert_false
@test()
async def test_assert_false_passes() -> None:
    assert_false(False)  # noqa: FBT003
    assert_false(0)
    assert_false("")
    assert_false([])
    assert_false(None)


@test()
async def test_assert_false_fails() -> None:
    try:
        assert_false(True)  # noqa: FBT003
    except AssertionFailure as exc:
        assert_true(exc.actual)
        assert_false(exc.expected)
        assert_equal(exc.operator, "is")
    else:
        assert_raise()


@test()
async def test_assert_false_fails_with_truthy_value() -> None:
    try:
        assert_false(1)
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(exc.actual, 1)
        assert_false(exc.expected)


@test()
async def test_assert_false_custom_message() -> None:
    try:
        assert_false("non-empty", msg="String should be empty")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(str(exc), "String should be empty")


# Test assert_in
@test()
async def test_assert_in_passes() -> None:
    assert_in(1, [1, 2, 3])
    assert_in("h", "hello")
    assert_in("key", {"key": "value"})
    assert_in(2, {1, 2, 3})


@test()
async def test_assert_in_fails() -> None:
    try:
        assert_in(5, [1, 2, 3])
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(exc.actual, 5)
        assert_equal(exc.expected, [1, 2, 3])
        assert_equal(exc.operator, "in")


@test()
async def test_assert_in_fails_string() -> None:
    try:
        assert_in("z", "hello")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(exc.actual, "z")
        assert_equal(exc.expected, "hello")


@test()
async def test_assert_in_custom_message() -> None:
    try:
        assert_in("missing", ["a", "b", "c"], msg="Item not in list")
        assert False, "Should have raised AssertionFailure"  # noqa: B011
    except AssertionFailure as exc:
        assert_equal(str(exc), "Item not in list")
