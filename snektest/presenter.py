from typing import cast

from rich.console import Console
from rich.traceback import Traceback

from snektest.models import FailedResult, PassedResult, TestResult

# Initialize console
console = Console()

_RESULTS: list[TestResult] = []


def print_error(exc: str) -> None:
    console.print(exc, markup=False, style="red")


def print_test_result(result: TestResult) -> None:
    console.print(
        f"{result.name!s} ... ", end="", markup=False, highlight=False, no_wrap=True
    )
    if isinstance(result.result, PassedResult):
        console.print(
            f"[green]OK[/green] ({result.duration:.2f}s)", highlight=False, no_wrap=True
        )
    else:
        console.print(
            f"[red]FAIL[/red] ({result.duration:.2f}s)", highlight=False, no_wrap=True
        )
    _RESULTS.append(result)


def print_failures() -> None:
    failures = [
        result for result in _RESULTS if isinstance(result.result, FailedResult)
    ]
    if not failures:
        return

    console.print()
    console.rule("[bold red]FAILURES", style="red")
    console.print()

    for result in failures:
        console.rule(f"[bold red]{result.name}", characters="=", style="red")

        # We already made sure to filter for failing results only
        failing_result = cast("FailedResult", result.result)
        tb = Traceback.from_exception(
            failing_result.exc_type, failing_result.exc_value, failing_result.traceback
        )
        console.print(tb)
        console.print()


def print_summary(total_duration: float) -> None:
    passed_count = sum(1 for _ in _RESULTS if isinstance(_.result, PassedResult))
    failed_count = sum(1 for _ in _RESULTS if isinstance(_.result, FailedResult))

    if failed_count > 0:
        console.rule("[wheat1]SUMMARY", style="wheat1")
        for result in _RESULTS:
            if (failed_result := result.result) and isinstance(
                failed_result, FailedResult
            ):
                error_desc = str(failed_result.exc_type)
                if str(failed_result.exc_value) != "":
                    error_desc += f": {failed_result.exc_value}"
                console.print("FAILED", style="red", end=" ")
                console.print(
                    f"{result.name} - {error_desc}",
                    markup=False,
                )
        console.print()

    status_color = "red" if failed_count > 0 else "green"
    status_text = f"[bold {status_color}]"
    if failed_count > 0:
        status_text += f"{failed_count} failed, "
    status_text += (
        f"{passed_count} passed in {total_duration:.2f}s[/bold {status_color}]"
    )

    console.rule(status_text, style=status_color)
