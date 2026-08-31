"""Tests for machine-bound benchmark baseline comparison and persistence."""

import json
import os
import subprocess
import tempfile
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from snektest import (
    assert_eq,
    assert_in,
    assert_is_not_none,
    assert_isinstance,
    assert_raises,
    fixture,
    load_fixture,
    test,
)
from snektest.benchmark import BenchmarkContext
from snektest.benchmark_baseline import BenchmarkBaseline, MachineFingerprint
from snektest.cli import TestRunSummary, build_json_summary
from snektest.diagnostics import snapshot_exception
from snektest.execution import execute_test
from snektest.fixtures import FixtureRegistry, use_registry
from snektest.models import (
    AssertionFailure,
    BadRequestError,
    BenchmarkComparison,
    BenchmarkMeasurement,
    ErrorResult,
    FailedResult,
    FilterItem,
    PassedResult,
    TestCase,
    TestName,
    TestResult,
)
from snektest.presenter import print_test_result_to_console


class _Clock:
    """Deterministic clock for a single benchmark round."""

    def __init__(self, readings: list[int]) -> None:
        self._readings: list[int] = readings
        self._index: int = 0

    def __call__(self) -> int:
        reading = self._readings[self._index]
        self._index += 1
        return reading


def _machine(*, logical_cpu_count: int = 8) -> MachineFingerprint:
    return MachineFingerprint(
        architecture="x86_64",
        logical_cpu_count=logical_cpu_count,
        processor="Test CPU",
        python_implementation="CPython",
        python_version="3.14.0",
        system="Linux",
    )


def _measurement(  # noqa: PLR0913
    *,
    median_seconds: float,
    name: str = "query",
    rounds: int = 5,
    warmup: int = 1,
    regression_below: float = 0.1,
    noise_floor_seconds: float = 0.0,
) -> BenchmarkMeasurement:
    return BenchmarkMeasurement(
        name=name,
        rounds=rounds,
        warmup=warmup,
        min_seconds=median_seconds,
        median_seconds=median_seconds,
        p95_seconds=median_seconds,
        mean_seconds=median_seconds,
        stddev_seconds=0.0,
        median_budget_seconds=1.0,
        p95_budget_seconds=None,
        disable_gc=True,
        median_regression_below=regression_below,
        regression_noise_floor_seconds=noise_floor_seconds,
    )


def _test_result(
    test_file: Path,
    *measurements: BenchmarkMeasurement,
    comparisons: tuple[BenchmarkComparison, ...] = (),
    function: str = "test_query",
    parameters: str = "small",
) -> TestResult:
    return TestResult(
        name=TestName(
            file_path=test_file,
            func_name=function,
            params_part=parameters,
        ),
        duration=0.1,
        result=PassedResult(
            benchmarks=measurements,
            benchmark_comparisons=comparisons,
        ),
        markers=("medium",),
        captured_output="",
        fixture_teardown_failures=(),
        fixture_teardown_output=None,
        warnings=(),
    )


def _project_file(root: Path, name: str = "test_perf.py") -> Path:
    _ = (root / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    test_file = root / name
    _ = test_file.write_text("")
    return test_file


def _create_baseline(
    root: Path,
    test_file: Path,
    measurement: BenchmarkMeasurement,
) -> BenchmarkBaseline:
    return BenchmarkBaseline.update(
        root / "benchmarks.json",
        project_root=root,
        test_results=[_test_result(test_file, measurement)],
        filter_items=[FilterItem(str(test_file))],
        mark=None,
        current_machine=_machine(),
    ).baseline


@test(mark="medium")
def test_update_writes_versioned_deterministic_baseline() -> None:
    """An update writes structured identity, protocol, statistics, and machine data."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)

        update = BenchmarkBaseline.update(
            root / "benchmarks.json",
            project_root=root,
            test_results=[_test_result(test_file, _measurement(median_seconds=0.001))],
            filter_items=[FilterItem(str(test_file))],
            mark=None,
            current_machine=_machine(),
        )
        payload = json.loads((root / "benchmarks.json").read_text())

    assert_eq(update.updated_entries, 1)
    assert_eq(payload["schema_version"], 1)
    assert_eq(payload["machine"]["processor"], "Test CPU")
    assert_eq(payload["benchmarks"][0]["identity"]["path"], "test_perf.py")
    assert_eq(payload["benchmarks"][0]["identity"]["parameters"], "small")
    assert_eq(payload["benchmarks"][0]["identity"]["name"], "query")
    assert_eq(payload["benchmarks"][0]["protocol"]["disable_gc"], True)
    assert_eq(payload["benchmarks"][0]["statistics"]["median_seconds"], 0.001)


@test(mark="medium")
def test_comparison_passes_below_relative_limit() -> None:
    """A current median below the configured relative limit passes."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(root, test_file, _measurement(median_seconds=0.001))

        comparison = baseline.compare(
            _test_result(test_file).name,
            _measurement(median_seconds=0.00109),
        )

    assert_eq(comparison.verdict, "passed")
    assert_eq(round(comparison.change_ratio, 2), 0.09)
    assert_eq(comparison.limit_seconds, 0.0011)


@test(mark="medium")
def test_noise_floor_allows_small_absolute_change() -> None:
    """The absolute floor wins when it exceeds the relative allowance."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(
            root, test_file, _measurement(median_seconds=0.000001)
        )

        comparison = baseline.compare(
            _test_result(test_file).name,
            _measurement(
                median_seconds=0.00000105,
                regression_below=0.01,
                noise_floor_seconds=0.0000001,
            ),
        )

    assert_eq(comparison.verdict, "passed")
    assert_eq(comparison.allowed_increase_seconds, 0.0000001)


@test(mark="medium")
async def test_regression_becomes_failure_with_measurement_and_comparison() -> None:
    """A relative regression fails at context exit without discarding its data."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = BenchmarkBaseline.update(
            root / "benchmarks.json",
            project_root=root,
            test_results=[
                TestResult(
                    name=TestName(
                        file_path=test_file,
                        func_name="benchmark_body",
                        params_part="",
                    ),
                    duration=0.1,
                    result=PassedResult(
                        benchmarks=(
                            _measurement(
                                median_seconds=0.000001,
                                rounds=1,
                                warmup=0,
                            ),
                        )
                    ),
                    markers=("medium",),
                    captured_output="",
                    fixture_teardown_failures=(),
                    fixture_teardown_output=None,
                    warnings=(),
                )
            ],
            filter_items=[FilterItem(str(test_file))],
            mark=None,
            current_machine=_machine(),
        ).baseline

        def benchmark_body() -> None:
            timing = BenchmarkContext(
                median_below=1,
                name="query",
                rounds=1,
                warmup=0,
                disable_gc=True,
                median_regression_below=0.1,
                clock=_Clock([0, 1_200]),
            )
            with timing:
                for _ in timing.rounds:
                    pass

        test_result = await execute_test(
            TestCase(
                function=benchmark_body,
                markers=("medium",),
                name=TestName(
                    file_path=test_file,
                    func_name="benchmark_body",
                    params_part="",
                ),
            ),
            benchmark_baseline=baseline,
        )

    failure = assert_isinstance(test_result.result, FailedResult)
    assert_in("regressed +20.0%", failure.exception.message)
    assert_eq(len(failure.benchmarks), 1)
    assert_eq(failure.benchmark_comparisons[0].verdict, "regressed")


@test(mark="medium")
async def test_caught_gate_failure_does_not_shift_later_comparison() -> None:
    """Comparison metadata remains attached to its exact completed measurement."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = BenchmarkBaseline.update(
            root / "benchmarks.json",
            project_root=root,
            test_results=[
                _test_result(
                    test_file,
                    _measurement(
                        median_seconds=0.000001,
                        rounds=1,
                        warmup=0,
                    ),
                    function="benchmark_body",
                    parameters="",
                )
            ],
            filter_items=[FilterItem(str(test_file))],
            mark=None,
            current_machine=_machine(),
        ).baseline

        def benchmark_body() -> None:
            too_slow = BenchmarkContext(
                median_below=0.000001,
                name="too slow",
                rounds=1,
                warmup=0,
                disable_gc=True,
                median_regression_below=0.1,
                clock=_Clock([0, 1_000]),
            )
            with assert_raises(AssertionFailure), too_slow:
                for _ in too_slow.rounds:
                    pass

            query = BenchmarkContext(
                median_below=1,
                name="query",
                rounds=1,
                warmup=0,
                disable_gc=True,
                median_regression_below=0.1,
                clock=_Clock([0, 1_000]),
            )
            with query:
                for _ in query.rounds:
                    pass

        test_result = await execute_test(
            TestCase(
                function=benchmark_body,
                markers=("medium",),
                name=TestName(
                    file_path=test_file,
                    func_name="benchmark_body",
                    params_part="",
                    resolved_file_path=test_file.resolve(),
                ),
            ),
            benchmark_baseline=baseline,
        )

    passed = assert_isinstance(test_result.result, PassedResult)
    assert_eq(len(passed.benchmarks), 2)
    assert_eq(len(passed.benchmark_comparisons), 1)
    assert_eq(passed.benchmark_comparisons[0].measurement_index, 1)
    summary = TestRunSummary(
        total_tests=1,
        passed=1,
        failed=0,
        errors=0,
        fixture_teardown_failed=0,
        session_teardown_failed=0,
        test_results=[test_result],
        session_teardown_failures=[],
    )
    benchmark_output = json.loads(json.dumps(build_json_summary(summary)))["tests"][0][
        "benchmark_measurements"
    ]
    assert_eq("baseline_comparison" in benchmark_output[0], False)
    assert_eq(benchmark_output[1]["baseline_comparison"]["verdict"], "passed")


@test(mark="medium")
def test_missing_baseline_is_a_failure() -> None:
    """An opted-in region missing from the snapshot cannot pass unchecked."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = BenchmarkBaseline(
            entries={},
            machine=_machine(),
            path=root / "benchmarks.json",
            project_root=root,
        )

        with assert_raises(AssertionFailure) as failure:
            _ = baseline.compare(
                _test_result(test_file).name,
                _measurement(median_seconds=0.001),
            )

    assert_in("no stored baseline", str(failure.exception))


@test(mark="medium")
def test_protocol_change_requires_baseline_update() -> None:
    """Rounds, warmup, and GC settings are part of measurement comparability."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(root, test_file, _measurement(median_seconds=0.001))

        with assert_raises(AssertionFailure) as failure:
            _ = baseline.compare(
                _test_result(test_file).name,
                _measurement(median_seconds=0.001, rounds=6),
            )

    assert_in("protocol changed", str(failure.exception))


@test(mark="medium")
def test_load_rejects_different_machine() -> None:
    """Raw timings from another machine class are never compared silently."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        _ = _create_baseline(root, test_file, _measurement(median_seconds=0.001))

        with assert_raises(BadRequestError) as failure:
            _ = BenchmarkBaseline.load(
                root / "benchmarks.json",
                project_root=root,
                current_machine=_machine(logical_cpu_count=16),
            )

    assert_in("machine does not match", str(failure.exception))


@test(mark="medium")
def test_identity_is_stable_across_project_worktrees() -> None:
    """Base and candidate checkouts share identity through project-relative paths."""
    with (
        tempfile.TemporaryDirectory() as base_directory,
        tempfile.TemporaryDirectory() as candidate_directory,
    ):
        base_root = Path(base_directory)
        candidate_root = Path(candidate_directory)
        base_file = _project_file(base_root)
        candidate_file = _project_file(candidate_root)
        baseline_path = base_root / "benchmarks.json"
        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=base_root,
            test_results=[_test_result(base_file, _measurement(median_seconds=0.001))],
            filter_items=[FilterItem(str(base_file))],
            mark=None,
            current_machine=_machine(),
        )

        baseline = BenchmarkBaseline.load(
            baseline_path,
            project_root=candidate_root,
            current_machine=_machine(),
        )
        comparison = baseline.compare(
            _test_result(candidate_file).name,
            _measurement(median_seconds=0.00105),
        )

    assert_eq(comparison.verdict, "passed")


@test(mark="medium")
def test_comparator_identity_does_not_follow_process_chdir() -> None:
    """A benchmark uses the canonical path captured during collection."""
    with (
        tempfile.TemporaryDirectory() as project_directory,
        tempfile.TemporaryDirectory() as other_directory,
    ):
        root = Path(project_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(root, test_file, _measurement(median_seconds=0.001))
        test_name = TestName(
            file_path=Path("test_perf.py"),
            func_name="test_query",
            params_part="small",
            resolved_file_path=test_file.resolve(),
        )
        original_directory = Path.cwd()
        try:
            os.chdir(other_directory)
            compare = baseline.comparator(test_name)
            comparison = compare(_measurement(median_seconds=0.00105))
        finally:
            os.chdir(original_directory)

    assert_eq(comparison.verdict, "passed")


@test(mark="medium")
def test_filtered_update_replaces_only_selected_scope() -> None:
    """A selected update prunes its file while preserving unrelated entries."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        first_file = _project_file(root, "test_first.py")
        second_file = _project_file(root, "test_second.py")
        baseline_path = root / "benchmarks.json"
        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[
                _test_result(first_file, _measurement(median_seconds=0.001)),
                _test_result(second_file, _measurement(median_seconds=0.002)),
            ],
            filter_items=[FilterItem(str(root))],
            mark=None,
            current_machine=_machine(),
        )

        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[_test_result(first_file)],
            filter_items=[FilterItem(str(first_file))],
            mark=None,
            current_machine=_machine(),
        )
        payload = json.loads(baseline_path.read_text())

    assert_eq(len(payload["benchmarks"]), 1)
    assert_eq(
        payload["benchmarks"][0]["identity"]["path"],
        "test_second.py",
    )


@test(mark="slow")
def test_directory_update_preserves_gitignored_baseline() -> None:
    """Broad updates do not prune explicit ignored-file benchmark entries."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        _ = subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            capture_output=True,
        )
        _ = (root / ".gitignore").write_text("ignored/\n")
        ignored_directory = root / "ignored"
        ignored_directory.mkdir()
        ignored_file = _project_file(ignored_directory)
        baseline_path = root / "benchmarks.json"
        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[
                _test_result(ignored_file, _measurement(median_seconds=0.001))
            ],
            filter_items=[FilterItem(str(ignored_file))],
            mark=None,
            current_machine=_machine(),
        )

        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[],
            filter_items=[FilterItem(str(root))],
            mark=None,
            current_machine=_machine(),
        )
        payload = json.loads(baseline_path.read_text())

    assert_eq(len(payload["benchmarks"]), 1)
    assert_eq(
        payload["benchmarks"][0]["identity"]["path"],
        "ignored/test_perf.py",
    )


@test(mark="medium")
def test_marked_update_prunes_only_observed_tests() -> None:
    """Marker filtering removes stale regions only from tests that actually ran."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        first_file = _project_file(root, "test_first.py")
        second_file = _project_file(root, "test_second.py")
        baseline_path = root / "benchmarks.json"
        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[
                _test_result(first_file, _measurement(median_seconds=0.001)),
                _test_result(second_file, _measurement(median_seconds=0.002)),
            ],
            filter_items=[FilterItem(str(root))],
            mark=None,
            current_machine=_machine(),
        )

        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[_test_result(first_file)],
            filter_items=[FilterItem(str(root))],
            mark="fast",
            current_machine=_machine(),
        )
        payload = json.loads(baseline_path.read_text())

    assert_eq(len(payload["benchmarks"]), 1)
    assert_eq(
        payload["benchmarks"][0]["identity"]["path"],
        "test_second.py",
    )


@test(mark="medium")
def test_load_rejects_malformed_schema() -> None:
    """Malformed and unsupported files are CLI configuration errors."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        baseline_path = root / "benchmarks.json"
        _ = baseline_path.write_text('{"schema_version": 2}')

        with assert_raises(BadRequestError) as failure:
            _ = BenchmarkBaseline.load(
                baseline_path,
                project_root=root,
                current_machine=_machine(),
            )

    assert_in("Invalid benchmark baseline", str(failure.exception))


@test(mark="medium")
def test_update_rejects_existing_writer_lock() -> None:
    """Concurrent writers fail instead of silently losing a filtered update."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline_path = root / "benchmarks.json"
        lock_path = root / ".benchmarks.json.lock"
        _ = lock_path.write_text("")

        with assert_raises(BadRequestError) as failure:
            _ = BenchmarkBaseline.update(
                baseline_path,
                project_root=root,
                test_results=[
                    _test_result(test_file, _measurement(median_seconds=0.001))
                ],
                filter_items=[FilterItem(str(test_file))],
                mark=None,
                current_machine=_machine(),
            )

    assert_in("already being updated", str(failure.exception))


@test(mark="medium")
def test_duplicate_region_name_is_rejected_in_baseline_mode() -> None:
    """Call order never disambiguates two persisted regions with one name."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(root, test_file, _measurement(median_seconds=0.001))
        compare = baseline.comparator(_test_result(test_file).name)
        _ = compare(_measurement(median_seconds=0.001))

        with assert_raises(BadRequestError) as failure:
            _ = compare(_measurement(median_seconds=0.001))

        next_run_comparison = baseline.comparator(_test_result(test_file).name)(
            _measurement(median_seconds=0.001)
        )

    assert_in("unique region names", str(failure.exception))
    assert_eq(next_run_comparison.verdict, "passed")


@test(mark="medium")
async def test_baseline_bad_request_still_tears_down_function_fixture() -> None:
    """Configuration errors raised at context exit cannot bypass fixture cleanup."""
    events: list[str] = []

    @fixture
    def resource() -> Generator[None]:
        events.append("setup")
        yield
        events.append("teardown")

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(
            root,
            test_file,
            _measurement(median_seconds=0.000001, rounds=1, warmup=0),
        )

        def benchmark_body() -> None:
            load_fixture(resource())
            for _ in range(2):
                timing = BenchmarkContext(
                    median_below=1,
                    name="query",
                    rounds=1,
                    warmup=0,
                    disable_gc=True,
                    median_regression_below=0.1,
                    clock=_Clock([0, 1_000]),
                )
                with timing:
                    for _ in timing.rounds:
                        pass

        test_case = TestCase(
            function=benchmark_body,
            markers=("medium",),
            name=TestName(
                file_path=test_file,
                func_name="test_query",
                params_part="small",
            ),
        )
        with use_registry(FixtureRegistry()), assert_raises(BadRequestError):
            _ = await execute_test(test_case, benchmark_baseline=baseline)

    assert_eq(events, ["setup", "teardown"])


@test(mark="medium")
async def test_teardown_failure_is_reported_with_baseline_bad_request() -> None:
    """A teardown exception is not discarded by a pending configuration error."""

    @fixture
    def broken_resource() -> Generator[None]:
        yield
        msg = "teardown failed"
        raise RuntimeError(msg)

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline = _create_baseline(
            root,
            test_file,
            _measurement(median_seconds=0.000001, rounds=1, warmup=0),
        )

        def benchmark_body() -> None:
            load_fixture(broken_resource())
            for _ in range(2):
                timing = BenchmarkContext(
                    median_below=1,
                    name="query",
                    rounds=1,
                    warmup=0,
                    disable_gc=True,
                    median_regression_below=0.1,
                    clock=_Clock([0, 1_000]),
                )
                with timing:
                    for _ in timing.rounds:
                        pass

        test_case = TestCase(
            function=benchmark_body,
            markers=("medium",),
            name=TestName(
                file_path=test_file,
                func_name="test_query",
                params_part="small",
                resolved_file_path=test_file.resolve(),
            ),
        )
        with use_registry(FixtureRegistry()):
            test_result = await execute_test(test_case, benchmark_baseline=baseline)

    error = assert_isinstance(test_result.result, ErrorResult)
    assert_eq(error.exception.type_name, "BadRequestError")
    assert_eq(len(test_result.fixture_teardown_failures), 1)
    assert_in(
        "teardown failed",
        test_result.fixture_teardown_failures[0].exception.message,
    )


@test(mark="medium")
def test_parameter_cases_have_distinct_baseline_identities() -> None:
    """Parameter names are a stored identity field, not parsed display text."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        test_file = _project_file(root)
        baseline_path = root / "benchmarks.json"
        _ = BenchmarkBaseline.update(
            baseline_path,
            project_root=root,
            test_results=[
                _test_result(
                    test_file,
                    _measurement(median_seconds=0.001),
                    parameters="small",
                ),
                _test_result(
                    test_file,
                    _measurement(median_seconds=0.002),
                    parameters="large",
                ),
            ],
            filter_items=[FilterItem(str(test_file))],
            mark=None,
            current_machine=_machine(),
        )
        payload = json.loads(baseline_path.read_text())

    assert_eq(
        [entry["identity"]["parameters"] for entry in payload["benchmarks"]],
        ["large", "small"],
    )


@test(mark="fast")
def test_failed_comparison_is_in_json_output() -> None:
    """Machine-readable failures retain the observation and baseline verdict."""
    failure = AssertionFailure("regressed")
    try:
        raise failure
    except AssertionFailure:
        traceback = assert_is_not_none(failure.__traceback__)
    benchmark_comparison = _measurement(median_seconds=0.0012)

    result = TestResult(
        name=TestName(
            file_path=Path("tests/test_perf.py"),
            func_name="test_query",
            params_part="",
        ),
        duration=0.1,
        result=FailedResult(
            exception=snapshot_exception(AssertionFailure, failure, traceback),
            benchmarks=(benchmark_comparison,),
            benchmark_comparisons=(
                BenchmarkComparison(
                    allowed_increase_seconds=0.0001,
                    baseline_median_seconds=0.001,
                    change_ratio=0.2,
                    limit_seconds=0.0011,
                    measurement_index=0,
                    name="query",
                    noise_floor_seconds=0.0,
                    observed_median_seconds=0.0012,
                    regression_below=0.1,
                    verdict="regressed",
                ),
            ),
        ),
        markers=("fast",),
        captured_output="",
        fixture_teardown_failures=(),
        fixture_teardown_output=None,
        warnings=(),
    )
    summary = TestRunSummary(
        total_tests=1,
        passed=0,
        failed=1,
        errors=0,
        fixture_teardown_failed=0,
        session_teardown_failed=0,
        test_results=[result],
        session_teardown_failures=[],
    )

    output = json.loads(json.dumps(build_json_summary(summary)))

    benchmark_output = output["tests"][0]["benchmark_measurements"][0]
    assert_eq(benchmark_output["median_seconds"], 0.0012)
    assert_eq(benchmark_output["baseline_comparison"]["verdict"], "regressed")


@test(mark="fast")
def test_passing_comparison_is_rendered_on_console_line() -> None:
    """The green result line shows baseline, delta, limit, and verdict."""
    comparison = BenchmarkComparison(
        allowed_increase_seconds=0.0001,
        baseline_median_seconds=0.001,
        change_ratio=0.05,
        limit_seconds=0.0011,
        measurement_index=0,
        name="query",
        noise_floor_seconds=0.00001,
        observed_median_seconds=0.00105,
        regression_below=0.1,
        verdict="passed",
    )
    result = _test_result(
        Path("tests/test_perf.py"),
        _measurement(
            median_seconds=0.00105,
            noise_floor_seconds=0.00001,
        ),
        comparisons=(comparison,),
    )
    console = Console(record=True)

    print_test_result_to_console(console, result)

    output = console.export_text()
    assert_in("baseline=1.0ms", output)
    assert_in("delta=+5.0%", output)
    assert_in("(<+10.0% or 10.0us) PASSED", output)


@test(mark="fast")
def test_failed_result_line_keeps_benchmark_diagnostics() -> None:
    """Completed measurements remain visible when a later assertion fails."""
    failure = AssertionFailure("later failure")
    try:
        raise failure
    except AssertionFailure:
        traceback = assert_is_not_none(failure.__traceback__)
    result = _test_result(
        Path("tests/test_perf.py"),
        _measurement(median_seconds=0.001),
    )
    passed = assert_isinstance(result.result, PassedResult)
    result = replace(
        result,
        result=FailedResult(
            exception=snapshot_exception(AssertionFailure, failure, traceback),
            benchmarks=passed.benchmarks,
        ),
    )
    console = Console(record=True)

    print_test_result_to_console(console, result)

    output = console.export_text()
    assert_in("FAIL", output)
    assert_in("benchmark[query]", output)


@test(mark="fast")
def test_ungated_duplicate_name_does_not_receive_comparison() -> None:
    """Comparison ordering distinguishes gated and ungated regions with one name."""
    gated = _measurement(median_seconds=0.00105)
    ungated = BenchmarkMeasurement(
        name="query",
        rounds=5,
        warmup=1,
        min_seconds=0.001,
        median_seconds=0.001,
        p95_seconds=0.001,
        mean_seconds=0.001,
        stddev_seconds=0,
        median_budget_seconds=1,
        p95_budget_seconds=None,
    )
    comparison = BenchmarkComparison(
        allowed_increase_seconds=0.0001,
        baseline_median_seconds=0.001,
        change_ratio=0.05,
        limit_seconds=0.0011,
        measurement_index=1,
        name="query",
        noise_floor_seconds=0,
        observed_median_seconds=0.00105,
        regression_below=0.1,
        verdict="passed",
    )
    result = _test_result(
        Path("tests/test_perf.py"),
        ungated,
        gated,
        comparisons=(comparison,),
    )
    output = json.loads(
        json.dumps(
            build_json_summary(
                TestRunSummary(
                    total_tests=1,
                    passed=1,
                    failed=0,
                    errors=0,
                    fixture_teardown_failed=0,
                    session_teardown_failed=0,
                    test_results=[result],
                    session_teardown_failures=[],
                )
            )
        )
    )

    benchmarks = output["tests"][0]["benchmark_measurements"]
    assert_eq("baseline_comparison" in benchmarks[0], False)
    assert_eq(benchmarks[1]["baseline_comparison"]["verdict"], "passed")
