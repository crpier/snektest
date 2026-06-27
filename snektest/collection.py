"""Test discovery and collection into executable test cases."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getmembers, isfunction
from pathlib import Path
from sys import modules
from typing import TypeGuard, cast

from pydantic import ValidationError

from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.models import CollectionError, FilterItem, TestCase, TestName
from snektest.utils import (
    get_test_function_markers,
    get_test_function_params,
    is_test_function,
)

TEST_FILE_PREFIX = "test_"

TestsQueue = asyncio.Queue[TestCase]


@dataclass(frozen=True)
class _CollectionMatchStats:
    """Selector match details for one collected file."""

    function_matched: bool
    params_matched: bool


def load_tests_from_file(  # noqa: PLR0913
    file_path: PyFilePath,
    filter_item: FilterItem,
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    mark: str | None = None,
    spec_loader: Callable[..., object] = spec_from_file_location,
) -> _CollectionMatchStats:
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

    test_functions = [
        func for _, func in getmembers(module, isfunction) if is_test_function(func)
    ]
    if filter_item.function_name is None:
        named_functions = test_functions
    else:
        named_functions = [
            func
            for func in test_functions
            if func.__name__ == filter_item.function_name
        ]

    params_matched = filter_item.params is None or any(
        filter_item.params in get_test_function_params(func) for func in named_functions
    )

    if mark is None:
        runnable_functions = named_functions
    else:
        runnable_functions = [
            func for func in named_functions if mark in get_test_function_markers(func)
        ]

    for func in runnable_functions:
        markers = get_test_function_markers(func)
        for param_names, params in get_test_function_params(func).items():
            if filter_item.params and filter_item.params != param_names:
                continue
            test_name = TestName(
                file_path=file_path, func_name=func.__name__, params_part=param_names
            )
            test_case = TestCase(
                function=func,
                markers=markers,
                name=test_name,
                param_values=tuple(param.value for param in params),
            )
            _ = loop.call_soon_threadsafe(queue.put_nowait, test_case)

    return _CollectionMatchStats(
        function_matched=filter_item.function_name is None or bool(named_functions),
        params_matched=params_matched,
    )


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
            function_matched = filter_item.function_name is None
            params_matched = filter_item.params is None
            for file_path in file_paths:
                stats = load_tests_from_file(
                    file_path=file_path,
                    filter_item=filter_item,
                    queue=queue,
                    loop=loop,
                    mark=mark,
                )
                function_matched = function_matched or stats.function_matched
                params_matched = params_matched or stats.params_matched
            if filter_item.function_name is not None and not function_matched:
                msg = (
                    f"No test named `{filter_item.function_name}` found for "
                    f"filter `{filter_item}`"
                )
                raise CollectionError(msg)  # noqa: TRY301
            if filter_item.params is not None and not params_matched:
                msg = (
                    f"No parameterized case `{filter_item.params}` found for "
                    f"filter `{filter_item}`"
                )
                raise CollectionError(msg)  # noqa: TRY301
    except BaseException as e:
        if exception_holder is not None:
            if isinstance(e, CollectionError):
                exception_holder.append(e)
            else:
                collection_error = CollectionError(f"Error during collection: {e}")
                collection_error.__cause__ = e
                exception_holder.append(collection_error)
    finally:
        _ = loop.call_soon_threadsafe(queue.shutdown)
