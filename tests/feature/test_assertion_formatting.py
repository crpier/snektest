"""Tests for assertion error formatting and diff presentation."""

from snektest import assert_eq, test
from snektest.assertions import assert_raise
from snektest.models import AssertionFailure


@test()
def test_list_diff_formatting() -> None:
    """Test that list comparison shows clear diff."""
    try:
        assert_eq(["pula"], ["pizda"])
    except AssertionFailure as exc:
        error_msg = str(exc)
        # Should show the difference clearly
        assert "['pula']" in error_msg
        assert "['pizda']" in error_msg
    else:
        assert_raise()


@test()
def test_nested_list_diff_formatting() -> None:
    """Test that nested list comparison shows clear diff at the right level."""
    try:
        assert_eq(
            [1, 2, [3, 4, "foo"]],
            [1, 2, [3, 4, "bar"]],
        )
    except AssertionFailure as exc:
        error_msg = str(exc)
        # Should show the nested difference
        assert "foo" in error_msg
        assert "bar" in error_msg
        # Should indicate where the difference is
        assert "At index 2" in error_msg or "[3, 4," in error_msg
    else:
        assert_raise()


@test()
def test_dict_diff_formatting() -> None:
    """Test that dict comparison shows clear diff."""
    try:
        assert_eq(
            {"name": "alice", "age": 30},
            {"name": "bob", "age": 30},
        )
    except AssertionFailure as exc:
        error_msg = str(exc)
        # Should show both values
        assert "alice" in error_msg
        assert "bob" in error_msg
    else:
        assert_raise()


@test()
def test_multiline_string_diff_formatting() -> None:
    """Test that multiline string comparison shows line-by-line diff."""
    try:
        assert_eq(
            "hello\nworld\nfoo",
            "hello\nworld\nbar",
        )
    except AssertionFailure as exc:
        error_msg = str(exc)
        # Should show the different lines
        assert "foo" in error_msg
        assert "bar" in error_msg
        # Should show context (the common lines)
        assert "hello" in error_msg or "world" in error_msg
    else:
        assert_raise()
