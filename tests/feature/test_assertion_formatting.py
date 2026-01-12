"""Tests for assertion error formatting and diff presentation."""
# TODO: all these tests should assert that the entire output matches the expected output

from snektest import assert_eq, assert_raises, test
from snektest.models import AssertionFailure


@test()
def test_list_diff_formatting() -> None:
    """Test that list comparison shows clear diff."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(["foo"], ["bar"])

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "['foo'] != ['bar']")


@test()
def test_nested_list_diff_formatting() -> None:
    """Test that nested list comparison shows clear diff at the right level."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(
            [1, 2, [3, 4, "foo"]],
            [1, 2, [3, 4, "bar"]],
        )

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "[1, 2, [3, 4, 'foo']] != [1, 2, [3, 4, 'bar']]")


@test()
def test_dict_diff_formatting() -> None:
    """Test that dict comparison shows clear diff."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(
            {"name": "alice", "age": 30},
            {"name": "bob", "age": 30},
        )

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "{'name': 'alice', 'age': 30} != {'name': 'bob', 'age': 30}")


@test()
def test_multiline_string_diff_formatting() -> None:
    """Test that multiline string comparison shows line-by-line diff."""
    with assert_raises(AssertionFailure) as exc_info:
        assert_eq(
            "hello\nworld\nfoo",
            "hello\nworld\nbar",
        )

    error_msg = str(exc_info.exception)
    assert_eq(error_msg, "'hello\\nworld\\nfoo' != 'hello\\nworld\\nbar'")
