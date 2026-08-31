"""Test discovery and collection into executable test cases."""

import asyncio
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec, spec_from_file_location
from inspect import isfunction
from pathlib import Path
from shutil import which
from sys import modules
from typing import TypeGuard, cast

from pydantic import ValidationError

from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.models import CollectionError, FilterItem, TestCase, TestName
from snektest.utils import (
    get_test_function_markers,
    get_test_function_mutex,
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


@dataclass(frozen=True)
class _CollectedFile:
    """Cases and selector-match details collected from one module."""

    cases: tuple[TestCase, ...]
    stats: _CollectionMatchStats


def git_ignored_files(file_paths: Sequence[Path], *, cwd: Path) -> frozenset[Path]:
    """Return ignored candidates according to Git, or none when Git is unavailable."""
    git_executable = which("git")
    if git_executable is None or not file_paths:
        return frozenset[Path]()

    encoded_paths = b"\0".join(os.fsencode(path.resolve()) for path in file_paths)
    try:
        process: subprocess.CompletedProcess[bytes] = subprocess.run(  # noqa: S603
            [git_executable, "check-ignore", "--stdin", "-z"],
            cwd=cwd,
            input=encoded_paths + b"\0",
            capture_output=True,
            check=False,
        )
    except OSError:
        return frozenset[Path]()
    if process.returncode not in {0, 1}:
        return frozenset[Path]()
    return frozenset(
        Path(os.fsdecode(path)).resolve()
        for path in process.stdout.split(b"\0")
        if path
    )


def collect_tests_from_file(
    file_path: PyFilePath,
    filter_item: FilterItem,
    *,
    mark: str | None = None,
    spec_loader: Callable[..., object] = spec_from_file_location,
    collection_root: Path | None = None,
) -> _CollectedFile:
    """Import one module and return selected cases in definition order."""
    path_root = collection_root or Path.cwd()
    canonical_file_path = (
        file_path.resolve()
        if file_path.is_absolute()
        else (path_root / file_path).resolve()
    )
    module_name = ".".join(canonical_file_path.with_suffix("").parts)
    if spec_loader is spec_from_file_location and module_name in modules:
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
        try:
            loader.exec_module(module)
        except BaseException:
            modules.pop(module_name, None)
            raise

    test_functions = [
        func
        for func in vars(module).values()
        if isfunction(func)
        and func.__module__ == module.__name__
        and is_test_function(func)
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

    cases: list[TestCase] = []
    for func in runnable_functions:
        markers = get_test_function_markers(func)
        for param_names, params in get_test_function_params(func).items():
            if filter_item.params and filter_item.params != param_names:
                continue
            test_name = TestName(
                file_path=file_path,
                func_name=func.__name__,
                params_part=param_names,
                resolved_file_path=canonical_file_path,
            )
            test_case = TestCase(
                function=func,
                markers=markers,
                mutex=get_test_function_mutex(func),
                name=test_name,
                param_values=tuple(param.value for param in params),
            )
            cases.append(test_case)

    return _CollectedFile(
        cases=tuple(cases),
        stats=_CollectionMatchStats(
            function_matched=filter_item.function_name is None or bool(named_functions),
            params_matched=params_matched,
        ),
    )


def load_tests_from_file(  # noqa: PLR0913
    file_path: PyFilePath,
    filter_item: FilterItem,
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    mark: str | None = None,
    spec_loader: Callable[..., object] = spec_from_file_location,
    collection_root: Path | None = None,
) -> _CollectionMatchStats:
    """Collect one file and publish its cases to an event-loop queue."""
    collected = collect_tests_from_file(
        file_path,
        filter_item,
        mark=mark,
        spec_loader=spec_loader,
        collection_root=collection_root,
    )
    for test_case in collected.cases:
        _ = loop.call_soon_threadsafe(queue.put_nowait, test_case)
    return collected.stats


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

    if not filter_item.file_path.is_dir():
        return (
            [filter_item.file_path] if path_is_runnable(filter_item.file_path) else []
        )

    paths = [
        path
        for path in (
            dirpath / name
            for dirpath, _, filenames in filter_item.file_path.walk()
            for name in filenames
        )
        if path_is_runnable(path)
    ]
    ignored_paths = git_ignored_files(paths, cwd=filter_item.file_path)

    return sorted(
        (path for path in paths if path.resolve() not in ignored_paths),
        key=lambda path: path.as_posix(),
    )


def collect_tests_from_filters(
    filter_items: list[FilterItem],
    *,
    mark: str | None = None,
) -> list[TestCase]:
    """Build one complete canonical plan before any selected test executes."""
    collected_cases: list[TestCase] = []
    try:
        for filter_item in filter_items:
            function_matched = filter_item.function_name is None
            params_matched = filter_item.params is None
            selection_names: set[TestName] = set()
            for file_path in generate_file_list(filter_item):
                collected_file = collect_tests_from_file(
                    file_path=file_path,
                    filter_item=filter_item,
                    mark=mark,
                    collection_root=Path.cwd().resolve(),
                )
                function_matched = (
                    function_matched or collected_file.stats.function_matched
                )
                params_matched = params_matched or collected_file.stats.params_matched
                for test_case in collected_file.cases:
                    if test_case.name in selection_names:
                        msg = (
                            f"Collected duplicate test case `{test_case.name}` for "
                            f"filter `{filter_item}`"
                        )
                        raise CollectionError(msg)  # noqa: TRY301
                    selection_names.add(test_case.name)
                    collected_cases.append(
                        replace(test_case, ordinal=len(collected_cases))
                    )
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
        if not collected_cases:
            msg = "No tests selected"
            raise CollectionError(msg)  # noqa: TRY301
    except CollectionError:
        raise
    except BaseException as exc:
        msg = f"Error during collection: {exc}"
        raise CollectionError(msg) from exc
    return collected_cases


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
        test_cases = collect_tests_from_filters(filter_items, mark=mark)
        for test_case in test_cases:
            _ = loop.call_soon_threadsafe(queue.put_nowait, test_case)
    except BaseException as exc:
        if exception_holder is not None:
            if isinstance(exc, CollectionError):
                exception_holder.append(exc)
            else:
                collection_error = CollectionError(f"Error during collection: {exc}")
                collection_error.__cause__ = exc
                exception_holder.append(collection_error)
    finally:
        _ = loop.call_soon_threadsafe(queue.shutdown)
