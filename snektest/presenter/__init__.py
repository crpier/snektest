from rich.console import Console

from snektest.models import (
    BenchmarkComparison,
    BenchmarkMeasurement,
    ErrorResult,
    FailedResult,
    MemoryMeasurement,
    PassedResult,
    TeardownFailure,
    TestResult,
)
from snektest.presenter.errors import print_failures as _print_failures
from snektest.presenter.summary import print_summary as _print_summary

console = Console()

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")
_BYTES_PER_UNIT = 1024
_MICROSECOND = 0.000001
_MILLISECOND = 0.001


def humanize_bytes(num_bytes: float) -> str:
    """Render a byte count as a compact 1024-based string, e.g. `8.2MB`, `12B`.

    Sub-kilobyte values are shown as whole bytes; larger values carry one
    decimal place. The sign is preserved so slopes read `+12B` / `-4B`.
    """
    magnitude = abs(num_bytes)
    unit_index = 0
    while magnitude >= _BYTES_PER_UNIT and unit_index < len(_BYTE_UNITS) - 1:
        magnitude /= _BYTES_PER_UNIT
        unit_index += 1
    sign = "-" if num_bytes < 0 else ""
    if unit_index == 0:
        return f"{sign}{int(magnitude)}B"
    return f"{sign}{magnitude:.1f}{_BYTE_UNITS[unit_index]}"


def _format_measurement(measurement: MemoryMeasurement) -> str:
    """Render one measurement's set budgets, e.g. `peak=8.2MB (<10.0MB)`."""
    parts: list[str] = []
    if measurement.peak_budget is not None:
        peak = humanize_bytes(measurement.peak_bytes)
        budget = humanize_bytes(measurement.peak_budget)
        parts.append(f"peak={peak} (<{budget})")
    if measurement.slope_budget is not None and measurement.growth_slope is not None:
        signed_slope = (
            f"+{humanize_bytes(measurement.growth_slope)}"
            if measurement.growth_slope >= 0
            else humanize_bytes(measurement.growth_slope)
        )
        slope_budget = humanize_bytes(measurement.slope_budget)
        parts.append(
            f"slope={signed_slope}/round (<{slope_budget}, {measurement.rounds} rounds)"
        )
    return "  ".join(parts)


def _format_measurements(measurements: tuple[MemoryMeasurement, ...]) -> str:
    """Join every measurement's line into an OK-line suffix (empty if none)."""
    rendered = [_format_measurement(m) for m in measurements]
    non_empty = [line for line in rendered if line]
    if not non_empty:
        return ""
    return "  " + "  ".join(non_empty)


def _humanize_seconds(seconds: float) -> str:
    """Render seconds with a compact unit suited to benchmark timings."""
    if seconds < _MICROSECOND:
        return f"{seconds * 1_000_000_000:.1f}ns"
    if seconds < _MILLISECOND:
        return f"{seconds * 1_000_000:.1f}us"
    if seconds < 1:
        return f"{seconds * 1_000:.1f}ms"
    return f"{seconds:.2f}s"


def _format_benchmark(
    benchmark: BenchmarkMeasurement,
    comparison: BenchmarkComparison | None = None,
) -> str:
    """Render one benchmark's statistics and configured budgets."""
    label = "benchmark" if benchmark.name is None else f"benchmark[{benchmark.name}]"
    median = f"median={_humanize_seconds(benchmark.median_seconds)}"
    if benchmark.median_budget_seconds is not None:
        median += f" (<{_humanize_seconds(benchmark.median_budget_seconds)})"
    p95 = f"p95={_humanize_seconds(benchmark.p95_seconds)}"
    if benchmark.p95_budget_seconds is not None:
        p95 += f" (<{_humanize_seconds(benchmark.p95_budget_seconds)})"
    round_word = "round" if benchmark.rounds == 1 else "rounds"
    warmup_word = "warmup" if benchmark.warmup == 1 else "warmups"
    parts = [
        label,
        f"min={_humanize_seconds(benchmark.min_seconds)}",
        median,
        p95,
        f"mean={_humanize_seconds(benchmark.mean_seconds)}",
        f"stddev={_humanize_seconds(benchmark.stddev_seconds)}",
        f"({benchmark.rounds} {round_word}, {benchmark.warmup} {warmup_word})",
    ]
    if comparison is not None:
        floor = ""
        if comparison.noise_floor_seconds > 0:
            floor = f" or {_humanize_seconds(comparison.noise_floor_seconds)}"
        parts.append(
            f"baseline={_humanize_seconds(comparison.baseline_median_seconds)} "
            f"delta={comparison.change_ratio:+.1%} "
            f"(<+{comparison.regression_below:.1%}{floor}) {comparison.verdict.upper()}"
        )
    return " ".join(parts)


def _format_benchmarks(
    benchmarks: tuple[BenchmarkMeasurement, ...],
    comparisons: tuple[BenchmarkComparison, ...] = (),
) -> str:
    """Join benchmark statistics into an OK-line suffix."""
    if not benchmarks:
        return ""
    comparisons_by_index = {
        comparison.measurement_index: comparison for comparison in comparisons
    }
    formatted: list[str] = []
    for index, benchmark in enumerate(benchmarks):
        comparison = comparisons_by_index.get(index)
        formatted.append(_format_benchmark(benchmark, comparison))
    return "  " + "  ".join(formatted)


def print_error(exc: str) -> None:
    """Print an error message in red."""
    console.print(exc, markup=False, style="red")


def print_test_result_to_console(console: Console, result: TestResult) -> None:
    console.print(
        f"{result.name!s} ... ",
        end="",
        markup=False,
        highlight=False,
        soft_wrap=True,
    )
    match result.result:
        case PassedResult(
            measurements=measurements,
            benchmarks=benchmarks,
            benchmark_comparisons=benchmark_comparisons,
        ):
            console.print(
                f"OK ({result.duration:.2f}s){_format_measurements(measurements)}{_format_benchmarks(benchmarks, benchmark_comparisons)}",
                highlight=False,
                style="green",
                markup=False,
                soft_wrap=True,
            )
        case FailedResult(
            benchmarks=benchmarks,
            benchmark_comparisons=benchmark_comparisons,
        ):
            console.print(
                f"FAIL ({result.duration:.2f}s){_format_benchmarks(benchmarks, benchmark_comparisons)}",
                highlight=False,
                style="red",
                markup=False,
                soft_wrap=True,
            )
        case ErrorResult(
            benchmarks=benchmarks,
            benchmark_comparisons=benchmark_comparisons,
        ):
            console.print(
                f"ERROR ({result.duration:.2f}s){_format_benchmarks(benchmarks, benchmark_comparisons)}",
                highlight=False,
                style="dark_orange",
                markup=False,
                soft_wrap=True,
            )


def print_test_result(result: TestResult) -> None:
    """Print the result of a single test."""
    print_test_result_to_console(console, result)


def print_failures(
    test_results: list[TestResult],
    session_teardown_failures: list[TeardownFailure] | None = None,
    session_teardown_output: str | None = None,
) -> None:
    """Print all failures."""
    _print_failures(
        console,
        test_results,
        session_teardown_failures=session_teardown_failures,
        session_teardown_output=session_teardown_output,
    )


def print_summary(
    test_results: list[TestResult],
    total_duration: float,
    session_teardown_failures: list[TeardownFailure] | None = None,
) -> None:
    """Print test summary."""
    _print_summary(
        console,
        test_results,
        total_duration,
        session_teardown_failures=session_teardown_failures,
    )


__all__ = [
    "console",
    "humanize_bytes",
    "print_error",
    "print_failures",
    "print_summary",
    "print_test_result",
    "print_test_result_to_console",
]
