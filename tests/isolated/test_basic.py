"""Unit tests for basic snektest functionality."""

from pathlib import Path
from typing import Any

from snektest import Param, test
from snektest.assertions import (
    assert_eq,
    assert_false,
    assert_ge,
    assert_gt,
    assert_in,
    assert_isinstance,
    assert_is,
    assert_is_none,
    assert_is_not,
    assert_is_not_none,
    assert_le,
    assert_len,
    assert_lt,
    assert_ne,
    assert_not_in,
    assert_not_isinstance,
    assert_raises,
    assert_true,
    fail,
)
from snektest.models import AssertionFailure, TestName
from snektest.utils import (
    get_test_function_params,
    is_test_function,
    mark_test_function,
)


# ===== Test utils.py functions =====
@test()
def test_mark_test_function_basic():
    """Test that mark_test_function properly marks a function."""

    def dummy_func() -> None:
        pass

    mark_test_function(dummy_func, ())
    assert_true(is_test_function(dummy_func))


@test()
def test_is_test_function_unmarked():
    """Test that unmarked functions return False."""

    def dummy_func() -> None:
        pass

    assert_false(is_test_function(dummy_func))


@test()
def test_get_test_function_params_no_params():
    """Test getting params from a test with no parameters."""

    def dummy_func() -> None:
        pass

    mark_test_function(dummy_func, ())
    result = get_test_function_params(dummy_func)
    assert_eq(result, {"": ()})


@test()
def test_get_test_function_params_single_param_list():
    """Test getting params from a test with one parameter list."""

    def dummy_func(x: int) -> None:
        pass

    params = ([Param(value=1, name="1"), Param(value=2, name="2")],)
    mark_test_function(dummy_func, params)

    result = get_test_function_params(dummy_func)
    assert_eq(len(result), 2)
    assert_in("1", result)
    assert_in("2", result)


# ===== Test Param.to_dict =====
@test()
def test_param_to_dict_empty():
    """Test Param.to_dict with no params."""
    result = Param.to_dict(())
    assert_eq(result, {"": ()})


@test()
def test_param_to_dict_single_list():
    """Test Param.to_dict with a single param list."""
    params = ([Param(value=1, name="one"), Param(value=2, name="two")],)
    result = Param.to_dict(params)

    assert_eq(len(result), 2)
    assert_in("one", result)
    assert_in("two", result)
    assert_eq(result["one"][0].value, 1)
    assert_eq(result["two"][0].value, 2)


@test()
def test_param_to_dict_multiple_lists():
    """Test Param.to_dict creates cartesian product of param lists."""
    params = (
        [Param(value=1, name="a"), Param(value=2, name="b")],
        [Param(value="x", name="X"), Param(value="y", name="Y")],
    )
    result = Param.to_dict(params)

    assert_eq(len(result), 4)
    assert_in("a, X", result)
    assert_in("a, Y", result)
    assert_in("b, X", result)
    assert_in("b, Y", result)

    # Verify the actual values
    assert_eq(result["a, X"][0].value, 1)
    assert_eq(result["a, X"][1].value, "x")


# ===== Test TestName =====
@test()
def test_testname_str_no_params():
    """Test TestName string representation without params."""
    name = TestName(
        file_path=Path("tests/test_foo.py"), func_name="test_bar", params_part=""
    )
    assert_eq(str(name), "tests/test_foo.py::test_bar")


@test()
def test_testname_str_with_params():
    """Test TestName string representation with params."""
    name = TestName(
        file_path=Path("tests/test_foo.py"),
        func_name="test_bar",
        params_part="x=1, y=2",
    )
    assert_eq(str(name), "tests/test_foo.py::test_bar[x=1, y=2]")


# ===== Test assertion functions =====
@test()
def test_assert_eq_passes():
    """Test assert_eq with equal values."""
    assert_eq(1, 1)
    assert_eq("hello", "hello")
    assert_eq([1, 2], [1, 2])


@test()
def test_assert_eq_fails():
    """Test assert_eq raises AssertionFailure on inequality."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(1, 2)

    assert_eq(exc_info.exception.actual, 1)
    assert_eq(exc_info.exception.expected, 2)
    assert_eq(exc_info.exception.operator, "==")


@test()
def test_assert_ne_passes():
    """Test assert_ne with unequal values."""
    assert_ne(1, 2)
    assert_ne("hello", "world")


@test()
def test_assert_ne_fails():
    """Test assert_ne raises AssertionFailure on equality."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_ne(5, 5)

    assert_eq(exc_info.exception.actual, 5)
    assert_eq(exc_info.exception.expected, 5)
    assert_eq(exc_info.exception.operator, "!=")


@test()
def test_assert_true_passes():
    """Test assert_true with True value."""
    assert_true(True)


@test()
def test_assert_true_fails_with_truthy():
    """Test assert_true fails even with truthy values (identity check)."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_true(1)

    assert_eq(exc_info.exception.actual, 1)
    assert_eq(exc_info.exception.expected, True)


@test()
def test_assert_false_passes():
    """Test assert_false with False value."""
    assert_false(False)


@test()
def test_assert_false_fails_with_falsy():
    """Test assert_false fails even with falsy values (identity check)."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_false(0)

    assert_eq(exc_info.exception.actual, 0)
    assert_eq(exc_info.exception.expected, False)


@test()
def test_assert_is_none_passes():
    """Test assert_is_none with None."""
    assert_is_none(None)


@test()
def test_assert_is_none_fails():
    """Test assert_is_none fails with non-None values."""
    with assert_raises(AssertionFailure):
        assert_is_none(0)


@test()
def test_assert_is_not_none_passes():
    """Test assert_is_not_none with non-None values."""
    assert_is_not_none(0)
    assert_is_not_none("")
    assert_is_not_none(False)


@test()
def test_assert_is_not_none_fails():
    """Test assert_is_not_none fails with None."""
    with assert_raises(AssertionFailure):
        assert_is_not_none(None)


@test()
def test_assert_is_passes():
    """Test assert_is with same object."""
    obj: list[int] = []
    assert_is(obj, obj)


@test()
def test_assert_is_fails():
    """Test assert_is fails with different objects."""
    with assert_raises(AssertionFailure):
        assert_is([], [])


@test()
def test_assert_is_not_passes():
    """Test assert_is_not with different objects."""
    assert_is_not([], [])


@test()
def test_assert_is_not_fails():
    """Test assert_is_not fails with same object."""
    obj: list[int] = []
    with assert_raises(AssertionFailure):
        assert_is_not(obj, obj)


@test()
def test_assert_lt_passes():
    """Test assert_lt with less than values."""
    assert_lt(1, 2)
    assert_lt("a", "b")


@test()
def test_assert_lt_fails():
    """Test assert_lt fails when not less than."""
    with assert_raises(AssertionFailure):
        assert_lt(2, 1)


@test()
def test_assert_gt_passes():
    """Test assert_gt with greater than values."""
    assert_gt(2, 1)
    assert_gt("b", "a")


@test()
def test_assert_gt_fails():
    """Test assert_gt fails when not greater than."""
    with assert_raises(AssertionFailure):
        assert_gt(1, 2)


@test()
def test_assert_le_passes():
    """Test assert_le with less than or equal values."""
    assert_le(1, 2)
    assert_le(2, 2)


@test()
def test_assert_le_fails():
    """Test assert_le fails when greater than."""
    with assert_raises(AssertionFailure):
        assert_le(3, 2)


@test()
def test_assert_ge_passes():
    """Test assert_ge with greater than or equal values."""
    assert_ge(2, 1)
    assert_ge(2, 2)


@test()
def test_assert_ge_fails():
    """Test assert_ge fails when less than."""
    with assert_raises(AssertionFailure):
        assert_ge(1, 2)


@test()
def test_assert_in_passes():
    """Test assert_in with member in container."""
    assert_in(1, [1, 2, 3])
    assert_in("a", "abc")
    assert_in("key", {"key": "value"})


@test()
def test_assert_in_fails():
    """Test assert_in fails when member not in container."""
    with assert_raises(AssertionFailure):
        assert_in(4, [1, 2, 3])


@test()
def test_assert_not_in_passes():
    """Test assert_not_in with member not in container."""
    assert_not_in(4, [1, 2, 3])
    assert_not_in("d", "abc")


@test()
def test_assert_not_in_fails():
    """Test assert_not_in fails when member in container."""
    with assert_raises(AssertionFailure):
        assert_not_in(1, [1, 2, 3])


@test()
def test_assert_isinstance_passes():
    """Test assert_isinstance with correct type."""
    assert_isinstance(1, int)
    assert_isinstance("hello", str)
    assert_isinstance([1, 2], list)


@test()
def test_assert_isinstance_fails():
    """Test assert_isinstance fails with wrong type."""
    with assert_raises(AssertionFailure):
        assert_isinstance(1, str)


@test()
def test_assert_isinstance_multiple_types():
    """Test assert_isinstance with tuple of types."""
    assert_isinstance(1, (int, str))
    assert_isinstance("hello", (int, str))


@test()
def test_assert_not_isinstance_passes():
    """Test assert_not_isinstance with wrong type."""
    assert_not_isinstance(1, str)
    assert_not_isinstance("hello", int)


@test()
def test_assert_not_isinstance_fails():
    """Test assert_not_isinstance fails with correct type."""
    with assert_raises(AssertionFailure):
        assert_not_isinstance(1, int)


@test()
def test_assert_len_passes():
    """Test assert_len with correct length."""
    assert_len([1, 2, 3], 3)
    assert_len("hello", 5)
    assert_len({"a": 1, "b": 2}, 2)


@test()
def test_assert_len_fails():
    """Test assert_len fails with wrong length."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_len([1, 2, 3], 5)

    assert_eq(exc_info.exception.actual, 3)
    assert_eq(exc_info.exception.expected, 5)


@test()
def test_fail_always_raises():
    """Test that fail always raises AssertionFailure."""
    with assert_raises(AssertionFailure):
        fail()


@test()
def test_fail_with_message():
    """Test fail with custom message."""
    with assert_raises(AssertionFailure) as exc_info:
        fail("Custom failure message")

    assert_eq(str(exc_info.exception), "Custom failure message")


@test()
def test_assertion_failure_custom_message():
    """Test that custom messages work in assertions."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(1, 2, msg="Values don't match")

    assert_eq(str(exc_info.exception), "Values don't match")
