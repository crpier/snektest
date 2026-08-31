"""Machine-bound benchmark baseline storage, comparison, and updates."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from os import O_CREAT, O_EXCL, O_WRONLY, close, cpu_count, fsync
from os import open as os_open
from pathlib import Path
from platform import machine, processor, python_implementation, python_version, system
from tempfile import NamedTemporaryFile
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from snektest.collection import git_ignored_files
from snektest.models import (
    AssertionFailure,
    BadRequestError,
    BenchmarkComparison,
    BenchmarkMeasurement,
    FilterItem,
    TestName,
    TestResult,
)


class _StrictSchema(BaseModel):
    """Reject schema drift and non-finite timing values when loading JSON."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class MachineFingerprint(_StrictSchema):
    """Hardware and interpreter fields that must match for wall-time comparison."""

    architecture: str = Field(min_length=1)
    logical_cpu_count: int = Field(gt=0)
    processor: str = Field(min_length=1)
    python_implementation: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    system: str = Field(min_length=1)

    @classmethod
    def capture(cls) -> Self:
        """Capture stable machine-class fields without hostnames or timestamps."""
        operating_system = system()
        cpu_model = processor().strip()
        if not cpu_model and operating_system == "Linux":
            try:
                for line in Path("/proc/cpuinfo").read_text().splitlines():
                    if line.startswith("model name"):
                        _, cpu_model = line.split(":", 1)
                        cpu_model = cpu_model.strip()
                        break
            except OSError:
                pass
        return cls(
            architecture=machine() or "unknown",
            logical_cpu_count=cpu_count() or 1,
            processor=cpu_model or "unknown",
            python_implementation=python_implementation(),
            python_version=python_version(),
            system=operating_system or "unknown",
        )


class _IdentitySchema(_StrictSchema):
    function: str = Field(min_length=1)
    name: str = Field(min_length=1)
    parameters: str
    path: str = Field(min_length=1)


class _ProtocolSchema(_StrictSchema):
    disable_gc: bool
    rounds: int = Field(gt=0)
    warmup: int = Field(ge=0)


class _StatisticsSchema(_StrictSchema):
    mean_seconds: float = Field(ge=0)
    median_seconds: float = Field(gt=0)
    min_seconds: float = Field(ge=0)
    p95_seconds: float = Field(ge=0)
    stddev_seconds: float = Field(ge=0)


class _StoredBenchmarkSchema(_StrictSchema):
    identity: _IdentitySchema
    protocol: _ProtocolSchema
    statistics: _StatisticsSchema


class _BaselineSchema(_StrictSchema):
    benchmarks: list[_StoredBenchmarkSchema]
    machine: MachineFingerprint
    schema_version: Literal[1]


@dataclass(frozen=True, order=True)
class BenchmarkIdentity:
    """Stable identity for one named benchmark region in one test case."""

    path: str
    function: str
    parameters: str
    name: str


@dataclass(frozen=True)
class BaselineUpdate:
    """Result of atomically merging measurements into a baseline file."""

    baseline: BenchmarkBaseline
    updated_entries: int


def discover_project_root(start: Path | None = None) -> Path:
    """Find the nearest `pyproject.toml`, falling back to the starting directory."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


@contextmanager
def _baseline_update_lock(path: Path) -> Generator[None]:
    """Serialize baseline read/merge/write cycles across processes."""
    lock_path = path.with_name(f".{path.name}.lock")
    file_descriptor: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor = os_open(lock_path, O_CREAT | O_EXCL | O_WRONLY, 0o600)
    except FileExistsError as error:
        msg = (
            f"Benchmark baseline `{path}` is already being updated. "
            f"Remove stale lock `{lock_path}` if no update is running."
        )
        raise BadRequestError(msg) from error
    except OSError as error:
        msg = f"Could not lock benchmark baseline `{path}`: {error}"
        raise BadRequestError(msg) from error
    try:
        yield
    finally:
        if file_descriptor is not None:
            with suppress(OSError):
                close(file_descriptor)
        with suppress(OSError):
            lock_path.unlink(missing_ok=True)


@dataclass
class BenchmarkBaseline:
    """Loaded baseline bound to one project root and exact machine fingerprint."""

    entries: dict[BenchmarkIdentity, _StoredBenchmarkSchema]
    machine: MachineFingerprint
    path: Path
    project_root: Path

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        project_root: Path,
        current_machine: MachineFingerprint | None = None,
    ) -> Self:
        """Load and validate a baseline, including exact machine compatibility."""
        resolved_path = path.resolve()
        try:
            stored = _BaselineSchema.model_validate_json(resolved_path.read_text())
        except OSError as error:
            msg = f"Could not read benchmark baseline `{path}`: {error}"
            raise BadRequestError(msg) from error
        except ValidationError as error:
            msg = f"Invalid benchmark baseline `{path}`: {error}"
            raise BadRequestError(msg) from error

        observed_machine = current_machine or MachineFingerprint.capture()
        if stored.machine != observed_machine:
            expected = json.dumps(
                stored.machine.model_dump(mode="json"), sort_keys=True
            )
            actual = json.dumps(
                observed_machine.model_dump(mode="json"), sort_keys=True
            )
            msg = (
                f"Benchmark baseline machine does not match this machine. "
                f"Stored: {expected}. Current: {actual}."
            )
            raise BadRequestError(msg)

        entries: dict[BenchmarkIdentity, _StoredBenchmarkSchema] = {}
        for benchmark in stored.benchmarks:
            identity = BenchmarkIdentity(
                path=benchmark.identity.path,
                function=benchmark.identity.function,
                parameters=benchmark.identity.parameters,
                name=benchmark.identity.name,
            )
            if identity in entries:
                msg = f"Benchmark baseline contains duplicate identity `{identity}`."
                raise BadRequestError(msg)
            entries[identity] = benchmark
        return cls(
            entries=entries,
            machine=observed_machine,
            path=resolved_path,
            project_root=project_root.resolve(),
        )

    def compare(
        self, test_name: TestName, measurement: BenchmarkMeasurement
    ) -> BenchmarkComparison:
        """Compare one opted-in median, rejecting missing or incompatible entries."""
        if measurement.name is None:
            msg = "Internal error: baseline comparison requires a named benchmark."
            raise BadRequestError(msg)
        return self._compare_identity(
            self._identity(test_name, measurement.name), measurement
        )

    def comparator(
        self, test_name: TestName
    ) -> Callable[[BenchmarkMeasurement], BenchmarkComparison]:
        """Freeze test identity before execution and scope names to that execution."""
        test_path, function, parameters = self._test_key(test_name)
        seen_names: set[str] = set()

        def compare(measurement: BenchmarkMeasurement) -> BenchmarkComparison:
            if measurement.name is None:
                msg = "Internal error: baseline comparison requires a named benchmark."
                raise BadRequestError(msg)
            if measurement.name in seen_names:
                msg = (
                    "Benchmark baseline mode requires unique region names; "
                    f"`{measurement.name}` was repeated in `{test_name}`."
                )
                raise BadRequestError(msg)
            seen_names.add(measurement.name)
            return self._compare_identity(
                BenchmarkIdentity(
                    path=test_path,
                    function=function,
                    parameters=parameters,
                    name=measurement.name,
                ),
                measurement,
            )

        return compare

    def _compare_identity(
        self, identity: BenchmarkIdentity, measurement: BenchmarkMeasurement
    ) -> BenchmarkComparison:
        if measurement.name is None or measurement.median_regression_below is None:
            msg = "Internal error: baseline comparison requires a named opted-in benchmark."
            raise BadRequestError(msg)

        stored = self.entries.get(identity)
        if stored is None:
            message = (
                f"benchmark[{identity.name}] has no stored baseline for "
                f"{identity.path}::{identity.function}"
            )
            raise AssertionFailure(message)

        current_protocol = _ProtocolSchema(
            disable_gc=measurement.disable_gc,
            rounds=measurement.rounds,
            warmup=measurement.warmup,
        )
        if stored.protocol != current_protocol:
            message = (
                f"benchmark[{identity.name}] protocol changed from "
                f"{stored.protocol.model_dump()} to {current_protocol.model_dump()}; "
                "update the benchmark baseline"
            )
            raise AssertionFailure(message)

        baseline_median = stored.statistics.median_seconds
        allowed_increase = max(
            baseline_median * measurement.median_regression_below,
            measurement.regression_noise_floor_seconds,
        )
        limit = baseline_median + allowed_increase
        change_ratio = measurement.median_seconds / baseline_median - 1
        verdict: Literal["passed", "regressed"] = (
            "passed" if measurement.median_seconds < limit else "regressed"
        )
        return BenchmarkComparison(
            allowed_increase_seconds=allowed_increase,
            baseline_median_seconds=baseline_median,
            change_ratio=change_ratio,
            limit_seconds=limit,
            measurement_index=-1,
            name=identity.name,
            noise_floor_seconds=measurement.regression_noise_floor_seconds,
            observed_median_seconds=measurement.median_seconds,
            regression_below=measurement.median_regression_below,
            verdict=verdict,
        )

    @classmethod
    def update(  # noqa: PLR0913
        cls,
        path: Path,
        *,
        project_root: Path,
        test_results: list[TestResult],
        filter_items: list[FilterItem],
        mark: str | None,
        current_machine: MachineFingerprint | None = None,
    ) -> BaselineUpdate:
        """Merge the selected successful run and atomically replace the JSON file."""
        observed_machine = current_machine or MachineFingerprint.capture()
        resolved_path = path.resolve()
        with _baseline_update_lock(resolved_path):
            return cls._update_locked(
                resolved_path,
                project_root=project_root,
                test_results=test_results,
                filter_items=filter_items,
                mark=mark,
                observed_machine=observed_machine,
            )

    @classmethod
    def _update_locked(  # noqa: PLR0913
        cls,
        resolved_path: Path,
        *,
        project_root: Path,
        test_results: list[TestResult],
        filter_items: list[FilterItem],
        mark: str | None,
        observed_machine: MachineFingerprint,
    ) -> BaselineUpdate:
        if resolved_path.exists():
            existing = cls.load(
                resolved_path,
                project_root=project_root,
                current_machine=observed_machine,
            )
            entries = dict(existing.entries)
        else:
            entries = {}

        current_entries: dict[BenchmarkIdentity, _StoredBenchmarkSchema] = {}
        observed_tests: set[tuple[str, str, str]] = set()
        identity_builder = cls(
            entries={},
            machine=observed_machine,
            path=resolved_path,
            project_root=project_root.resolve(),
        )
        for test_result in test_results:
            observed_tests.add(identity_builder._test_key(test_result.name))
            for measurement in test_result.result.benchmarks:
                if measurement.median_regression_below is None:
                    continue
                if measurement.name is None:
                    msg = "Baseline updates require names for regression-gated benchmarks."
                    raise BadRequestError(msg)
                identity = identity_builder._identity(
                    test_result.name, measurement.name
                )
                if identity in current_entries:
                    msg = f"Benchmark baseline mode requires unique identity `{identity}`."
                    raise BadRequestError(msg)
                current_entries[identity] = cls._stored_entry(identity, measurement)

        if mark is None:
            entries = {
                identity: entry
                for identity, entry in entries.items()
                if not identity_builder._selected_by(identity, filter_items)
            }
        else:
            entries = {
                identity: entry
                for identity, entry in entries.items()
                if (identity.path, identity.function, identity.parameters)
                not in observed_tests
            }
        entries.update(current_entries)

        baseline = cls(
            entries=entries,
            machine=observed_machine,
            path=resolved_path,
            project_root=project_root.resolve(),
        )
        baseline._write()
        return BaselineUpdate(
            baseline=baseline,
            updated_entries=len(current_entries),
        )

    def _identity(self, test_name: TestName, region_name: str) -> BenchmarkIdentity:
        path, function, parameters = self._test_key(test_name)
        return BenchmarkIdentity(
            path=path,
            function=function,
            parameters=parameters,
            name=region_name,
        )

    def _test_key(self, test_name: TestName) -> tuple[str, str, str]:
        """Canonicalize a test independently of any benchmark region name."""
        test_path = test_name.resolved_file_path or test_name.file_path.resolve()
        try:
            relative_path = test_path.relative_to(self.project_root)
        except ValueError as error:
            msg = (
                f"Benchmark test `{test_name.file_path}` is outside project root "
                f"`{self.project_root}`."
            )
            raise BadRequestError(msg) from error
        return (
            relative_path.as_posix(),
            test_name.func_name,
            test_name.params_part,
        )

    def _selected_by(
        self, identity: BenchmarkIdentity, filter_items: list[FilterItem]
    ) -> bool:
        identity_path = self.project_root / identity.path
        for filter_item in filter_items:
            filter_path = filter_item.file_path.resolve()
            path_matches = (
                identity_path == filter_path
                if filter_path.is_file()
                else identity_path.is_relative_to(filter_path)
            )
            if (
                path_matches
                and filter_path.is_dir()
                and identity_path.resolve()
                in git_ignored_files([identity_path], cwd=filter_path)
            ):
                path_matches = False
            function_matches = (
                filter_item.function_name is None
                or filter_item.function_name == identity.function
            )
            parameters_match = (
                filter_item.params is None or filter_item.params == identity.parameters
            )
            if path_matches and function_matches and parameters_match:
                return True
        return False

    @staticmethod
    def _stored_entry(
        identity: BenchmarkIdentity, measurement: BenchmarkMeasurement
    ) -> _StoredBenchmarkSchema:
        return _StoredBenchmarkSchema(
            identity=_IdentitySchema(
                function=identity.function,
                name=identity.name,
                parameters=identity.parameters,
                path=identity.path,
            ),
            protocol=_ProtocolSchema(
                disable_gc=measurement.disable_gc,
                rounds=measurement.rounds,
                warmup=measurement.warmup,
            ),
            statistics=_StatisticsSchema(
                mean_seconds=measurement.mean_seconds,
                median_seconds=measurement.median_seconds,
                min_seconds=measurement.min_seconds,
                p95_seconds=measurement.p95_seconds,
                stddev_seconds=measurement.stddev_seconds,
            ),
        )

    def _write(self) -> None:
        """Serialize in stable order and atomically replace the destination."""
        stored = _BaselineSchema(
            benchmarks=[self.entries[key] for key in sorted(self.entries)],
            machine=self.machine,
            schema_version=1,
        )
        serialized = json.dumps(
            stored.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w",
                delete=False,
                dir=self.path.parent,
                encoding="utf-8",
                prefix=f".{self.path.name}.",
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                _ = temporary_file.write(f"{serialized}\n")
                temporary_file.flush()
                fsync(temporary_file.fileno())
            temporary_path.replace(self.path)
        except OSError as error:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            msg = f"Could not write benchmark baseline `{self.path}`: {error}"
            raise BadRequestError(msg) from error
