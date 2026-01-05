"""Unit tests for snektest models."""

from pathlib import Path
from tempfile import TemporaryDirectory

from snektest import test
from snektest.assertions import assert_eq, assert_raises
from snektest.models import ArgsError, FilterItem


@test()
def test_filter_item_directory():
    """Test FilterItem with a directory path."""
    filter_item = FilterItem("tests")
    assert_eq(filter_item.file_path, Path("tests"))
    assert_eq(filter_item.function_name, None)
    assert_eq(filter_item.params, None)


@test()
def test_filter_item_file():
    """Test FilterItem with a file path."""
    filter_item = FilterItem("tests/isolated/test_basic.py")
    assert_eq(filter_item.file_path, Path("tests/isolated/test_basic.py"))
    assert_eq(filter_item.function_name, None)
    assert_eq(filter_item.params, None)


@test()
def test_filter_item_with_function():
    """Test FilterItem with file and function name."""
    filter_item = FilterItem("tests/isolated/test_basic.py::test_assert_eq_passes")
    assert_eq(filter_item.file_path, Path("tests/isolated/test_basic.py"))
    assert_eq(filter_item.function_name, "test_assert_eq_passes")
    assert_eq(filter_item.params, None)


@test()
def test_filter_item_with_params():
    """Test FilterItem with file, function, and params."""
    filter_item = FilterItem("tests/isolated/test_basic.py::test_func[param1, param2]")
    assert_eq(filter_item.file_path, Path("tests/isolated/test_basic.py"))
    assert_eq(filter_item.function_name, "test_func")
    assert_eq(filter_item.params, "param1, param2")


@test()
def test_filter_item_str_simple():
    """Test FilterItem string representation for simple path."""
    filter_item = FilterItem("tests")
    assert_eq(str(filter_item), "tests")


@test()
def test_filter_item_str_with_function():
    """Test FilterItem string representation with function."""
    filter_item = FilterItem("tests/isolated/test_basic.py::test_func")
    assert_eq(str(filter_item), "tests/isolated/test_basic.py::test_func")


@test()
def test_filter_item_str_with_params():
    """Test FilterItem string representation with params."""
    filter_item = FilterItem("tests/isolated/test_basic.py::test_func[params]")
    assert_eq(str(filter_item), "tests/isolated/test_basic.py::test_func[params]")


@test()
def test_filter_item_nonexistent_path():
    """Test FilterItem raises error for nonexistent path."""
    with assert_raises(ArgsError) as exc_info:
        FilterItem("nonexistent/path/to/file.py")

    msg = str(exc_info.exception)
    assert_eq("provided path does not exist" in msg, True)


@test()
def test_filter_item_non_python_file():
    """Test FilterItem raises error for non-Python file."""
    with TemporaryDirectory() as tmpdir:
        # Create a non-Python file
        test_file = Path(tmpdir) / "test_file.txt"
        test_file.write_text("content")

        with assert_raises(ArgsError) as exc_info:
            FilterItem(str(test_file))

        msg = str(exc_info.exception)
        assert_eq("file is not a Python script" in msg, True)


@test()
def test_filter_item_file_not_starting_with_test():
    """Test FilterItem raises error for Python file not starting with test_."""
    with TemporaryDirectory() as tmpdir:
        # Create a Python file that doesn't start with test_
        test_file = Path(tmpdir) / "myfile.py"
        test_file.write_text("# python code")

        with assert_raises(ArgsError) as exc_info:
            FilterItem(str(test_file))

        msg = str(exc_info.exception)
        assert_eq("file does not start with _test" in msg, True)


@test()
def test_filter_item_empty_after_double_colon():
    """Test FilterItem raises error when nothing follows ::."""
    with assert_raises(ArgsError) as exc_info:
        FilterItem("tests/isolated/test_basic.py::")

    msg = str(exc_info.exception)
    assert_eq("nothing given after semicolon" in msg, True)


@test()
def test_filter_item_unterminated_bracket():
    """Test FilterItem raises error for unterminated bracket."""
    with assert_raises(ArgsError) as exc_info:
        FilterItem("tests/isolated/test_basic.py::test_func[param")

    msg = str(exc_info.exception)
    assert_eq("unterminated" in msg, True)


@test()
def test_filter_item_invalid_identifier():
    """Test FilterItem raises error for invalid function name."""
    with assert_raises(ArgsError) as exc_info:
        FilterItem("tests/isolated/test_basic.py::123invalid")

    msg = str(exc_info.exception)
    assert_eq("invalid identifier" in msg, True)


@test()
def test_filter_item_repr():
    """Test FilterItem repr includes all components."""
    filter_item = FilterItem("tests/isolated/test_basic.py::test_func[params]")
    repr_str = repr(filter_item)

    # Check that repr contains the key parts
    assert_eq("FilterItem" in repr_str, True)
    assert_eq("test_basic.py" in repr_str, True)
    assert_eq("test_func" in repr_str, True)
    assert_eq("params" in repr_str, True)
