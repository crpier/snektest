"""Tests for assertion error formatting and diff presentation."""
# TODO: all these tests should assert that the entire output matches the expected output

from typing import Any

from rich.console import Console

from snektest import assert_eq, assert_raises, test
from snektest.models import AssertionFailure
from snektest.presenter.diff import render_assertion_failure


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


@test()
def test_render_assertion_failure_defensive_paths() -> None:
    console = Console(record=True)

    def ndiff_stub(*args: Any, **kwargs: Any) -> list[str]:
        _ = (args, kwargs)
        return ["xx"]

    render_assertion_failure(
        console,
        AssertionFailure("msg", actual=[1], expected=[2]),
        ndiff_func=ndiff_stub,
    )

    render_assertion_failure(
        console,
        AssertionFailure("msg", actual={"a": 1}, expected={"a": 2}),
        ndiff_func=ndiff_stub,
    )


@test()
def test_render_assertion_failure_rich_diff_paths() -> None:
    console = Console(record=True)

    # List diff: index mismatch branch.
    render_assertion_failure(
        console,
        AssertionFailure("msg", actual=[1, 2], expected=[1, 3]),
    )

    # List diff: length mismatch branches.
    render_assertion_failure(
        console,
        AssertionFailure("msg", actual=[1], expected=[1, 2]),
    )
    render_assertion_failure(
        console,
        AssertionFailure("msg", actual=[1, 2], expected=[1]),
    )

    long_list = list(range(60))

    def ndiff_full(*args: Any, **kwargs: Any) -> list[str]:
        _ = (args, kwargs)
        return ["- x", "+ y", "? z", "  w", "xx"]

    render_assertion_failure(
        console,
        AssertionFailure(
            "msg",
            actual={"a": long_list, "b": 2},
            expected={"a": long_list, "b": 3},
        ),
        ndiff_func=ndiff_full,
    )

    def ndiff_multiline(*args: Any, **kwargs: Any) -> list[str]:
        _ = (args, kwargs)
        return ["+ a", "- b", "? c", "  d"]

    render_assertion_failure(
        console,
        AssertionFailure("msg", actual="hallo\nworld", expected="hello\nworld"),
        ndiff_func=ndiff_multiline,
    )
