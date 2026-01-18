import asyncio
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getmembers, isfunction
from pathlib import Path
from sys import modules
from types import FunctionType
from typing import TypeGuard, cast

from pydantic import ValidationError

from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.models import CollectionError, FilterItem, TestName
from snektest.utils import (
    get_test_function_markers,
    get_test_function_params,
    is_test_function,
)

TEST_FILE_PREFIX = "test_"

FuncTestEntry = tuple[TestName, FunctionType]
TestsQueue = asyncio.Queue[FuncTestEntry]


def load_tests_from_file(  # noqa: PLR0913
    file_path: PyFilePath,
    filter_item: FilterItem,
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    mark: str | None = None,
    spec_loader: Callable[..., object] = spec_from_file_location,
) -> None:
    """Load and queue tests from a single Python file."""
    module_name = ".".join(file_path.with_suffix("").parts)
    if module_name in modules:
        module = modules[module_name]
    else:
        spec = spec_loader(module_name, file_path)
        spec_value = cast("ModuleSpec", spec)
        loader = getattr(spec_value, "loader", None)
        if loader is None:
            msg = f"Could not load spec from {file_path}"
            raise CollectionError(msg)

        module = module_from_spec(spec_value)
        modules[module_name] = module
        loader.exec_module(module)

    runnable_functions = [func for _, func in getmembers(module, isfunction)]
    runnable_functions = filter(is_test_function, runnable_functions)
    if filter_item.function_name:
        runnable_functions = filter(
            lambda func: func.__name__ == filter_item.function_name, runnable_functions
        )

    if mark is not None:
        runnable_functions = filter(
            lambda func: mark in get_test_function_markers(func), runnable_functions
        )

    for func in runnable_functions:
        for param_names in get_test_function_params(func):
            if filter_item.params and filter_item.params != param_names:
                continue
            test_name = TestName(
                file_path=file_path, func_name=func.__name__, params_part=param_names
            )
            _ = loop.call_soon_threadsafe(queue.put_nowait, (test_name, func))


def generate_file_list(filter_item: FilterItem) -> list[PyFilePath]:
    """Generate a list of valid file paths for given filter item."""

    def path_is_runnable(file_path: Path) -> TypeGuard[PyFilePath]:
        if not file_path.name.startswith(TEST_FILE_PREFIX):
            return False
        try:
            file_path = validate_PyFilePath(file_path)
        except ValidationError:
            return False
        return True

    if filter_item.file_path.is_dir():
        paths = [
            dirpath / name
            for dirpath, _, filenames in filter_item.file_path.walk()
            for name in filenames
        ]
    else:
        paths = [filter_item.file_path]

    return [path for path in paths if path_is_runnable(path)]


def load_tests_from_filters(
    filter_items: list[FilterItem],
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    mark: str | None = None,
    exception_holder: list[BaseException] | None = None,
) -> None:
    """Load tests from all filter items and populate the queue.

    Args:
        filter_items: List of filter items to load tests from
        queue: Queue to populate with tests
        loop: Event loop for thread-safe queue operations
        exception_holder: Optional list to store exception if one occurs during collection
    """
    try:
        for filter_item in filter_items:
            file_paths = generate_file_list(filter_item)
            for file_path in file_paths:
                load_tests_from_file(
                    file_path=file_path,
                    filter_item=filter_item,
                    queue=queue,
                    loop=loop,
                    mark=mark,
                )
    except BaseException as e:
        if exception_holder is not None:
            exception_holder.append(e)
    finally:
        _ = loop.call_soon_threadsafe(queue.shutdown)
