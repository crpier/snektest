"""Versioned JSON adapter for normalized Snektest runs and CLI errors."""

from __future__ import annotations

from snektest._version import __version__
from snektest.models import (
    BenchmarkBaselineRun,
    CollectionDiagnostics,
    ErrorResult,
    ExceptionDiagnostic,
    ExpectedFailureResult,
    FailedResult,
    PassedResult,
    RunResult,
    SkippedResult,
    TestCase,
    TestResult,
    UnexpectedPassResult,
)

SUPPORTED_SCHEMA_VERSIONS = (1,)
"""Structured-output contracts accepted by this installation."""

SCHEMA_VERSION = SUPPORTED_SCHEMA_VERSIONS[-1]
"""Current structured-output contract version."""


def _json_exception(exception: ExceptionDiagnostic) -> dict[str, object]:
    """Serialize the complete bounded diagnostic retained by execution."""
    return {
        "type": exception.type_name,
        "qualified_type": exception.qualified_type_name,
        "message": exception.message,
        "traceback": [
            {
                "file": frame.filename,
                "function": frame.function_name,
                "line": frame.lineno,
                "source": frame.source_line,
            }
            for frame in exception.frames
        ],
        "notes": list(exception.notes),
        "cause": _json_exception(exception.cause) if exception.cause else None,
        "context": _json_exception(exception.context) if exception.context else None,
        "exceptions": [
            _json_exception(grouped_exception)
            for grouped_exception in exception.exceptions
        ],
    }


def _json_test_entry(test_result: TestResult) -> dict[str, object]:  # noqa: C901
    entry: dict[str, object] = {
        "name": str(test_result.name),
        "duration": test_result.duration,
        "captured_output": test_result.captured_output,
        "fixture_teardown_output": test_result.fixture_teardown_output,
        "markers": list(test_result.markers),
        "status": test_result.status,
        "warnings": list(test_result.warnings),
    }
    match test_result.result:
        case FailedResult(exception=exception) | ErrorResult(exception=exception):
            entry["exception"] = _json_exception(exception)
        case ExpectedFailureResult(reason=reason, exception=exception):
            entry["reason"] = reason
            if exception is not None:
                entry["exception"] = _json_exception(exception)
        case SkippedResult(reason=reason) | UnexpectedPassResult(reason=reason):
            entry["reason"] = reason
        case PassedResult(measurements=measurements):
            if measurements:
                entry["memory_measurements"] = [
                    {
                        "peak_bytes": measurement.peak_bytes,
                        "growth_slope": measurement.growth_slope,
                        "rounds": measurement.rounds,
                        "peak_budget": measurement.peak_budget,
                        "slope_budget": measurement.slope_budget,
                    }
                    for measurement in measurements
                ]
    benchmarks = test_result.result.benchmarks
    comparisons_by_index = {
        comparison.measurement_index: comparison
        for comparison in test_result.result.benchmark_comparisons
    }
    if benchmarks:
        benchmark_entries: list[dict[str, object]] = []
        for index, benchmark in enumerate(benchmarks):
            benchmark_entry: dict[str, object] = {
                "name": benchmark.name,
                "rounds": benchmark.rounds,
                "warmup": benchmark.warmup,
                "disable_gc": benchmark.disable_gc,
                "min_seconds": benchmark.min_seconds,
                "median_seconds": benchmark.median_seconds,
                "p95_seconds": benchmark.p95_seconds,
                "mean_seconds": benchmark.mean_seconds,
                "stddev_seconds": benchmark.stddev_seconds,
                "median_budget_seconds": benchmark.median_budget_seconds,
                "p95_budget_seconds": benchmark.p95_budget_seconds,
                "median_regression_below": benchmark.median_regression_below,
                "regression_noise_floor_seconds": (
                    benchmark.regression_noise_floor_seconds
                ),
            }
            comparison = comparisons_by_index.get(index)
            if comparison is not None:
                benchmark_entry["baseline_comparison"] = {
                    "verdict": comparison.verdict,
                    "baseline_median_seconds": comparison.baseline_median_seconds,
                    "observed_median_seconds": comparison.observed_median_seconds,
                    "change_ratio": comparison.change_ratio,
                    "regression_below": comparison.regression_below,
                    "noise_floor_seconds": comparison.noise_floor_seconds,
                    "allowed_increase_seconds": comparison.allowed_increase_seconds,
                    "limit_seconds": comparison.limit_seconds,
                }
            benchmark_entries.append(benchmark_entry)
        entry["benchmark_measurements"] = benchmark_entries
    if test_result.background_failures:
        entry["background_failures"] = [
            {
                "origin": failure.origin,
                "label": failure.label,
                "exception": _json_exception(failure.exception),
            }
            for failure in test_result.background_failures
        ]
    if test_result.fixture_teardown_failures:
        entry["fixture_teardown_failures"] = [
            {
                "fixture_name": failure.fixture_name,
                "exception": _json_exception(failure.exception),
            }
            for failure in test_result.fixture_teardown_failures
        ]
    return entry


def build_json_error(
    *,
    category: str,
    exit_code: int,
    message: str,
    type_name: str,
    exception: ExceptionDiagnostic | None = None,
) -> dict[str, object]:
    """Build the common versioned envelope for one CLI error."""
    error: dict[str, object]
    if exception is None:
        error = {
            "type": type_name,
            "qualified_type": type_name,
            "message": message,
            "traceback": [],
            "notes": [],
            "cause": None,
            "context": None,
            "exceptions": [],
        }
    else:
        error = _json_exception(exception)
    error["category"] = category
    return {
        "schema_version": SCHEMA_VERSION,
        "framework_version": __version__,
        "kind": "error",
        "exit_code": exit_code,
        "uncaptured_output": "",
        "collection_output": "",
        "collection_warnings": [],
        "error": error,
    }


def build_json_collection(
    test_cases: list[TestCase],
    diagnostics: CollectionDiagnostics,
    *,
    uncaptured_output: str = "",
) -> dict[str, object]:
    """Build the versioned document for a completed collection-only command."""
    return {
        "schema_version": SCHEMA_VERSION,
        "framework_version": __version__,
        "kind": "collection",
        "exit_code": 0,
        "total_tests": len(test_cases),
        "uncaptured_output": uncaptured_output,
        "collection_output": diagnostics.output,
        "collection_warnings": list(diagnostics.warnings),
        "tests": [
            {
                "name": str(test_case.name),
                "markers": list(test_case.markers),
            }
            for test_case in test_cases
        ],
    }


def build_json_summary(
    run_result: RunResult,
    *,
    uncaptured_output: str = "",
) -> dict[str, object]:
    """Build the versioned document for one normalized completed run."""
    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "framework_version": __version__,
        "kind": "test_run",
        "exit_code": run_result.exit_code,
        "total_tests": run_result.total_tests,
        "selected_tests": run_result.selected_tests,
        "stopped_early": run_result.stopped_early,
        "total_duration": run_result.total_duration,
        "uncaptured_output": uncaptured_output,
        "collection_output": run_result.collection_output,
        "collection_warnings": list(run_result.collection_warnings),
        "warnings": list(run_result.warnings),
        "passed": run_result.passed,
        "skipped": run_result.skipped,
        "expected_failures": run_result.expected_failures,
        "unexpected_passes": run_result.unexpected_passes,
        "failed": run_result.failed,
        "errors": run_result.errors,
        "fixture_teardown_failed": run_result.fixture_teardown_failed,
        "run_teardown_failed": run_result.run_teardown_failed,
        "session_teardown_failed": run_result.session_teardown_failed,
        "run_teardown_output": run_result.run_teardown_output,
        "run_teardown_warnings": list(run_result.run_teardown_warnings),
        "session_teardown_output": run_result.session_teardown_output,
        "session_teardown_warnings": list(run_result.session_teardown_warnings),
        "run_teardown_failures": [
            {
                "fixture_name": failure.fixture_name,
                "exception": _json_exception(failure.exception),
            }
            for failure in run_result.run_teardown_failures
        ],
        "session_teardown_failures": [
            {
                "fixture_name": failure.fixture_name,
                "exception": _json_exception(failure.exception),
            }
            for failure in run_result.session_teardown_failures
        ],
        "tests": [_json_test_entry(result) for result in run_result.test_results],
    }
    baseline = run_result.benchmark_baseline
    if isinstance(baseline, BenchmarkBaselineRun):
        machine_output: dict[str, object] | None = None
        if baseline.machine is not None:
            machine_output = {
                "architecture": baseline.machine.architecture,
                "logical_cpu_count": baseline.machine.logical_cpu_count,
                "processor": baseline.machine.processor,
                "python_implementation": baseline.machine.python_implementation,
                "python_version": baseline.machine.python_version,
                "system": baseline.machine.system,
            }
        output["benchmark_baseline"] = {
            "mode": baseline.mode,
            "path": baseline.path,
            "machine": machine_output,
            "written": baseline.written,
            "updated_entries": baseline.updated_entries,
        }
    return output


__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "build_json_collection",
    "build_json_error",
    "build_json_summary",
]
