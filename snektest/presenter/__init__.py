from rich.console import Console

from snektest.models import (
    ErrorResult,
    FailedResult,
    PassedResult,
    TeardownFailure,
    TestResult,
)
from snektest.presenter.errors import print_failures as _print_failures
from snektest.presenter.summary import print_summary as _print_summary

console = Console()
BYTE_UNIT = 1024


def _format_bytes(byte_count: float, *, sign: bool = False) -> str:
    absolute_bytes = abs(byte_count)
    units = ("B", "KB", "MB", "GB", "TB")
    unit_index = 0
    scaled_bytes = byte_count
    while absolute_bytes >= BYTE_UNIT and unit_index < len(units) - 1:
        absolute_bytes /= BYTE_UNIT
        scaled_bytes /= BYTE_UNIT
        unit_index += 1
    prefix = "+" if sign and scaled_bytes >= 0 else ""
    if unit_index == 0:
        return f"{prefix}{scaled_bytes:.0f}{units[unit_index]}"
    return f"{prefix}{scaled_bytes:.1f}{units[unit_index]}"


def _format_memory_measurements(result: PassedResult) -> str:
    parts: list[str] = []
    for measurement in result.measurements:
        if measurement.peak_budget is not None:
            parts.append(
                f"peak={_format_bytes(measurement.peak_bytes)} (<{_format_bytes(measurement.peak_budget)})"
            )
        if (
            measurement.slope_budget is not None
            and measurement.growth_slope is not None
        ):
            parts.append(
                f"slope={_format_bytes(measurement.growth_slope, sign=True)}/round (<{_format_bytes(measurement.slope_budget)}, {measurement.rounds} rounds)"
            )
    if not parts:
        return ""
    return "  " + "  ".join(parts)


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
        case PassedResult() as passed:
            console.print(
                f"OK ({result.duration:.2f}s){_format_memory_measurements(passed)}",
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
    "print_error",
    "print_failures",
    "print_summary",
    "print_test_result",
    "print_test_result_to_console",
]
