import asyncio
import json
import sys
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import cast

from snektest.collection import TestsQueue, load_tests_from_filters
from snektest.execution import run_tests
from snektest.models import (
    ArgsError,
    BadRequestError,
    CollectionError,
    ErrorResult,
    FailedResult,
    FilterItem,
    PassedResult,
    TeardownFailure,
    TestResult,
    UnreachableError,
)
from snektest.presenter import print_error


def _json_result_status(result: TestResult) -> str:
    match result.result:
        case PassedResult():
            return "passed"
        case FailedResult():
            return "failed"
        case ErrorResult():
            return "error"


def build_json_summary(summary: TestRunSummary) -> dict[str, object]:
    return {
        "passed": summary.passed,
        "failed": summary.failed,
        "errors": summary.errors,
        "fixture_teardown_failed": summary.fixture_teardown_failed,
        "session_teardown_failed": summary.session_teardown_failed,
        "tests": [
            {
                "name": str(result.name),
                "duration": result.duration,
                "markers": list(result.markers),
                "status": _json_result_status(result),
            }
            for result in summary.test_results
        ],
    }


@dataclass
class TestRunSummary:
    """Summary of test run results."""

    total_tests: int
    passed: int
    failed: int
    errors: int
    fixture_teardown_failed: int
    session_teardown_failed: int
    test_results: list[TestResult]
    session_teardown_failures: list[TeardownFailure]


@dataclass(frozen=True)
class CliOptions:
    capture_output: bool = True
    json_output: bool = False
    pdb_on_failure: bool = False
    mark: str | None = None


def _parse_mark_value(
    argv: list[str], index: int, mark: str | None
) -> tuple[str | None, int] | int:
    if mark is not None:
        print_error("Only one --mark value is supported")
        return 2
    if index + 1 >= len(argv):
        print_error("Missing value for --mark")
        return 2
    mark_value = argv[index + 1]
    if mark_value.startswith("-"):
        print_error("Missing value for --mark")
        return 2
    return mark_value, index + 1


def parse_cli_args(argv: list[str]) -> tuple[list[str], CliOptions] | int:
    capture_output = True
    json_output = False
    pdb_on_failure = False
    mark: str | None = None
    potential_filter: list[str] = []

    index = 0
    while index < len(argv):
        command = argv[index]
        if command.startswith("-"):
            match command:
                case "-s":
                    capture_output = False
                case "--json-output":
                    json_output = True
                case "--pdb":
                    pdb_on_failure = True
                case "--mark":
                    parsed = _parse_mark_value(argv, index, mark)
                    if isinstance(parsed, int):
                        return parsed
                    mark, index = parsed
                case _:
                    print_error(f"Invalid option: `{command}`")
                    return 2
        else:
            potential_filter.append(command)
        index += 1

    if not potential_filter:
        potential_filter.append(".")

    options = CliOptions(
        capture_output=capture_output,
        json_output=json_output,
        pdb_on_failure=pdb_on_failure,
        mark=mark,
    )
    return potential_filter, options


async def _run_tests_with_producer_thread(
    filter_items: list[FilterItem],
    *,
    capture_output: bool,
    pdb_on_failure: bool,
    mark: str | None = None,
) -> tuple[list[TestResult], list[TeardownFailure]]:
    queue = TestsQueue()
    collection_exception: list[BaseException] = []

    producer_thread = threading.Thread(
        target=load_tests_from_filters,
        kwargs={
            "filter_items": filter_items,
            "queue": queue,
            "loop": asyncio.get_running_loop(),
            "mark": mark,
            "exception_holder": collection_exception,
        },
    )

    producer_thread.start()

    try:
        test_results, session_teardown_failures = await run_tests(
            queue=queue,
            capture_output=capture_output,
            pdb_on_failure=pdb_on_failure,
        )
    finally:
        producer_thread.join()
        if collection_exception:
            raise collection_exception[0]

    return test_results, session_teardown_failures


def exit_code_from_summary(summary: TestRunSummary) -> int:
    has_failures = (
        summary.failed > 0
        or summary.errors > 0
        or summary.fixture_teardown_failed > 0
        or summary.session_teardown_failed > 0
    )
    return 1 if has_failures else 0


async def run_tests_programmatic(
    filter_items: list[FilterItem],
    *,
    capture_output: bool = True,
    pdb_on_failure: bool = False,
    mark: str | None = None,
) -> TestRunSummary:
    """Run tests and return structured results instead of printing.

    This is the programmatic API for testing snektest itself.
    Returns structured data instead of just printing and exiting.

    Args:
        filter_items: List of filter items to run tests from
        capture_output: Whether to capture test output

    Returns:
        TestRunSummary with test results and counts
    """
    test_results, session_teardown_failures = await _run_tests_with_producer_thread(
        filter_items,
        capture_output=capture_output,
        pdb_on_failure=pdb_on_failure,
        mark=mark,
    )

    return TestRunSummary(
        total_tests=len(test_results),
        passed=sum(1 for r in test_results if isinstance(r.result, PassedResult)),
        failed=sum(1 for r in test_results if isinstance(r.result, FailedResult)),
        errors=sum(1 for r in test_results if isinstance(r.result, ErrorResult)),
        fixture_teardown_failed=sum(
            1 for r in test_results if r.fixture_teardown_failures
        ),
        session_teardown_failed=len(session_teardown_failures),
        test_results=test_results,
        session_teardown_failures=session_teardown_failures,
    )


async def run_script(
    argv: list[str] | None = None,
    *,
    run_tests_programmatic_fn: Callable[..., Coroutine[object, object, object]]
    | None = None,
) -> int:
    """Parse arguments and run tests."""
    parsed = parse_cli_args(sys.argv[1:] if argv is None else argv)
    if isinstance(parsed, int):
        return parsed

    potential_filter, options = parsed

    try:
        filter_items = [FilterItem(item) for item in potential_filter]
    except ArgsError as e:
        print_error(str(e))
        return 2

    runner = run_tests_programmatic_fn or run_tests_programmatic
    try:
        summary = cast(
            "TestRunSummary",
            await runner(
                filter_items,
                capture_output=options.capture_output,
                pdb_on_failure=options.pdb_on_failure,
                mark=options.mark,
            ),
        )
    except asyncio.CancelledError:
        return 2

    if options.json_output:
        print(json.dumps(build_json_summary(summary)))

    return exit_code_from_summary(summary)


def main() -> None:
    """Main entry point for the CLI."""
    async_runner = cast("Callable[[object], int]", asyncio.run)
    sys.exit(main_inner(async_runner=async_runner))


def main_inner(
    *,
    async_runner: Callable[[object], int],
    argv: list[str] | None = None,
) -> int:
    try:
        coroutine = run_script(argv)
        return async_runner(coroutine)
    except CollectionError as e:
        print_error(f"Collection error: {e}")
        return 2
    except BadRequestError as e:
        print_error(f"Bad request error: {e}")
        return 2
    except UnreachableError as e:
        print_error(f"Internal error: {e}")
        return 2
    except KeyboardInterrupt:
        print_error("Interrupted by user")
        return 2
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    main()
