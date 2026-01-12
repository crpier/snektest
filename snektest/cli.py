import asyncio
import json
import logging
import sys
import threading
from dataclasses import dataclass

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
    logging_level: int = logging.WARNING
    capture_output: bool = True
    json_output: bool = False
    pdb_on_failure: bool = False


def parse_cli_args(argv: list[str]) -> tuple[list[str], CliOptions] | int:
    logging_level = logging.WARNING
    capture_output = True
    json_output = False
    pdb_on_failure = False
    potential_filter: list[str] = []

    for command in argv:
        if command.startswith("-"):
            match command:
                case "-v":
                    logging_level = logging.INFO
                case "-vv":
                    logging_level = logging.DEBUG
                case "-s":
                    capture_output = False
                case "--json-output":
                    json_output = True
                case "--pdb":
                    pdb_on_failure = True
                case _:
                    print_error(f"Invalid option: `{command}`")
                    return 2
        else:
            potential_filter.append(command)

    if not potential_filter:
        potential_filter.append(".")

    options = CliOptions(
        logging_level=logging_level,
        capture_output=capture_output,
        json_output=json_output,
        pdb_on_failure=pdb_on_failure,
    )
    return potential_filter, options


async def _run_tests_with_producer_thread(
    filter_items: list[FilterItem],
    *,
    logger: logging.Logger,
    capture_output: bool,
    pdb_on_failure: bool,
) -> tuple[list[TestResult], list[TeardownFailure]]:
    queue = TestsQueue()
    collection_exception: list[BaseException] = []

    producer_thread = threading.Thread(
        target=load_tests_from_filters,
        kwargs={
            "filter_items": filter_items,
            "queue": queue,
            "loop": asyncio.get_running_loop(),
            "logger": logger,
            "exception_holder": collection_exception,
        },
    )
    producer_thread.start()

    try:
        test_results, session_teardown_failures = await run_tests(
            queue=queue,
            logger=logger,
            capture_output=capture_output,
            pdb_on_failure=pdb_on_failure,
        )
    finally:
        producer_thread.join()
        # Re-raise any exception that occurred during collection
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
    logger = logging.getLogger("snektest")

    test_results, session_teardown_failures = await _run_tests_with_producer_thread(
        filter_items,
        logger=logger,
        capture_output=capture_output,
        pdb_on_failure=pdb_on_failure,
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


async def run_script() -> int:
    """Parse arguments and run tests."""
    parsed = parse_cli_args(sys.argv[1:])
    if isinstance(parsed, int):
        return parsed

    potential_filter, options = parsed
    logging.basicConfig(level=options.logging_level)
    logger = logging.getLogger("snektest")

    try:
        filter_items = [FilterItem(item) for item in potential_filter]
    except ArgsError as e:
        print_error(str(e))
        return 2
    logger.info("Filters=%s", filter_items)

    try:
        summary = await run_tests_programmatic(
            filter_items,
            capture_output=options.capture_output,
            pdb_on_failure=options.pdb_on_failure,
        )
    except asyncio.CancelledError:
        logger.info("Execution stopped")
        return 2

    if options.json_output:
        print(
            json.dumps(
                {
                    "passed": summary.passed,
                    "failed": summary.failed,
                    "errors": summary.errors,
                    "fixture_teardown_failed": summary.fixture_teardown_failed,
                    "session_teardown_failed": summary.session_teardown_failed,
                }
            )
        )

    return exit_code_from_summary(summary)


def main() -> None:
    """Main entry point for the CLI."""
    try:
        exit_code = asyncio.run(run_script())
    except CollectionError as e:
        print_error(f"Collection error: {e}")
        sys.exit(2)
    except BadRequestError as e:
        print_error(f"Bad request error: {e}")
        sys.exit(2)
    except UnreachableError as e:
        print_error(f"Internal error: {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        print_error("Interrupted by user")
        sys.exit(2)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(2)
    else:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
