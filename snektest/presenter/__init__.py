from rich.console import Console

from snektest.models import (
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
        case PassedResult(measurements=measurements):
            console.print(
                f"OK ({result.duration:.2f}s){_format_measurements(measurements)}",
                highlight=False,
                style="green",
                markup=False,
                soft_wrap=True,
            )
        case FailedResult():
            console.print(
                f"FAIL ({result.duration:.2f}s)",
                highlight=False,
                style="red",
                markup=False,
                soft_wrap=True,
            )
        case ErrorResult():
            console.print(
                f"ERROR ({result.duration:.2f}s)",
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
