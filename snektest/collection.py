"""Test discovery and collection into executable test cases."""

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec, spec_from_file_location
from inspect import isfunction
from pathlib import Path
from shutil import which
from sys import modules
from threading import Lock
from types import ModuleType
from typing import TypeGuard, cast

from pydantic import ValidationError

from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.decorators import reset_run_fixture_catalog
from snektest.models import (
    BadRequestError,
    CollectionError,
    EmptyCollectionError,
    FilterItem,
    InvalidTestDefinitionError,
    TestCase,
    TestName,
)
from snektest.output import maybe_capture_output
from snektest.utils import (
    get_test_function_markers,
    get_test_function_mutex,
    get_test_function_params,
    get_test_function_xfail,
    is_test_function,
)

TEST_FILE_PREFIX = "test_"

_COLLECTION_LOCK = Lock()


@dataclass
class _CollectionModuleLoader:
    """Import test modules once per canonical path within one collection run."""

    _cache: dict[Path, ModuleType] = field(default_factory=dict)
    _initialized_roots: set[Path] = field(default_factory=set)

    @staticmethod
    def _package_context(file_path: Path) -> tuple[Path, tuple[str, ...]]:
        package_parts: list[str] = []
        cursor = file_path.parent
        while (cursor / "__init__.py").is_file():
            package_parts.append(cursor.name)
            cursor = cursor.parent
        return cursor, tuple(reversed(package_parts))

    @staticmethod
    def _namespace(root: Path) -> str:
        digest = sha256(str(root).encode()).hexdigest()[:16]
        return f"_snektest_collection_{digest}"

    def _initialize_root(self, root: Path, namespace: str) -> None:
        if root in self._initialized_roots:
            return
        for module_name in tuple(modules):
            if module_name == namespace or module_name.startswith(f"{namespace}."):
                modules.pop(module_name, None)
        root_module = ModuleType(namespace)
        root_module.__package__ = namespace
        root_module.__path__ = [str(root)]
        root_module.__spec__ = ModuleSpec(namespace, loader=None, is_package=True)
        modules[namespace] = root_module
        self._initialized_roots.add(root)

    def load(
        self,
        file_path: Path,
        *,
        spec_loader: Callable[..., object],
    ) -> ModuleType:
        canonical_file_path = file_path.resolve()
        cached = self._cache.get(canonical_file_path)
        if cached is not None:
            return cached

        root, package_parts = self._package_context(canonical_file_path)
        namespace = self._namespace(root)
        self._initialize_root(root, namespace)
        if package_parts:
            package_name = ".".join((namespace, *package_parts))
            _ = import_module(package_name)
            module_name = f"{package_name}.{canonical_file_path.stem}"
            existing_module = modules.get(module_name)
            existing_file = getattr(existing_module, "__file__", None)
            if (
                isinstance(existing_module, ModuleType)
                and isinstance(existing_file, str)
                and Path(existing_file).resolve() == canonical_file_path
            ):
                self._cache[canonical_file_path] = existing_module
                return existing_module
        else:
            leaf_digest = sha256(str(canonical_file_path).encode()).hexdigest()[:16]
            module_name = f"{namespace}._test_{leaf_digest}"
        spec = spec_loader(module_name, canonical_file_path)
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
        self._cache[canonical_file_path] = module
        return module


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


def collect_tests_from_file(  # noqa: PLR0913
    file_path: PyFilePath,
    filter_item: FilterItem,
    *,
    mark: str | None = None,
    spec_loader: Callable[..., object] = spec_from_file_location,
    collection_root: Path | None = None,
    module_loader: _CollectionModuleLoader | None = None,
) -> _CollectedFile:
    """Import one module and return selected cases in definition order."""
    path_root = collection_root or Path.cwd()
    canonical_file_path = (
        file_path.resolve()
        if file_path.is_absolute()
        else (path_root / file_path).resolve()
    )
    active_module_loader = module_loader or _CollectionModuleLoader()
    module = active_module_loader.load(
        canonical_file_path,
        spec_loader=spec_loader,
    )

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
                expected_failure_reason=get_test_function_xfail(func),
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


def _record_empty_filter(
    filter_item: FilterItem,
    *,
    collected_before_filter: int,
    collected_cases: list[TestCase],
    empty_filters: list[FilterItem],
) -> None:
    if len(collected_cases) == collected_before_filter:
        empty_filters.append(filter_item)


def _validate_nonempty_plan(
    collected_cases: list[TestCase],
    empty_filters: list[FilterItem],
    *,
    allow_empty: bool,
) -> None:
    if allow_empty:
        return
    if empty_filters:
        msg = f"No tests selected for filter `{empty_filters[0]}`"
        raise EmptyCollectionError(msg)
    if not collected_cases:
        msg = "No tests selected"
        raise EmptyCollectionError(msg)


def collect_tests_from_filters(
    filter_items: list[FilterItem],
    *,
    allow_empty: bool = False,
    mark: str | None = None,
) -> list[TestCase]:
    """Build one complete canonical plan before any selected test executes."""
    collected_cases: list[TestCase] = []
    empty_filters: list[FilterItem] = []
    module_loader = _CollectionModuleLoader()
    reset_run_fixture_catalog()
    try:
        for filter_item in filter_items:
            collected_before_filter = len(collected_cases)
            function_matched = filter_item.function_name is None
            params_matched = filter_item.params is None
            selection_names: set[TestName] = set()
            for file_path in generate_file_list(filter_item):
                collected_file = collect_tests_from_file(
                    file_path=file_path,
                    filter_item=filter_item,
                    mark=mark,
                    collection_root=Path.cwd().resolve(),
                    module_loader=module_loader,
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
            _record_empty_filter(
                filter_item,
                collected_before_filter=collected_before_filter,
                collected_cases=collected_cases,
                empty_filters=empty_filters,
            )
        _validate_nonempty_plan(collected_cases, empty_filters, allow_empty=allow_empty)
    except CollectionError:
        raise
    except BadRequestError as exc:
        msg = f"Invalid test definition: {exc}"
        raise InvalidTestDefinitionError(msg) from exc
    except BaseException as exc:
        msg = f"Error during collection: {exc}"
        raise CollectionError(msg) from exc
    return collected_cases


def collect_test_plan(
    filter_items: list[FilterItem],
    *,
    allow_empty: bool = False,
    capture_output: bool = False,
    mark: str | None = None,
) -> list[TestCase]:
    """Collect one complete plan under process-global import/output guards."""
    with _COLLECTION_LOCK, maybe_capture_output(capture_output):
        return collect_tests_from_filters(
            filter_items,
            allow_empty=allow_empty,
            mark=mark,
        )
