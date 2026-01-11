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


async def run_script() -> int:  # noqa: PLR0912, C901, PLR0915, PLR0914
    """Parse arguments and run tests."""
    logging_level = logging.WARNING
    potential_filter: list[str] = []
    capture_output = True
    json_output = False
    pdb_on_failure = False
    for command in sys.argv[1:]:
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
    logging.basicConfig(level=logging_level)
    logger = logging.getLogger("snektest")

    try:
        filter_items = [FilterItem(item) for item in potential_filter]
    except ArgsError as e:
        print_error(str(e))
        return 2
    logger.info("Filters=%s", filter_items)

    # Use programmatic API if JSON output requested
    if json_output:
        summary = await run_tests_programmatic(
            filter_items,
            capture_output=capture_output,
            pdb_on_failure=pdb_on_failure,
        )
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
        return 0 if (summary.failed == 0 and summary.errors == 0) else 1
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
    except asyncio.CancelledError:
        logger.info("Execution stopped")
        return 2
    finally:
        producer_thread.join()
        logger.info("Producer thread ended. Exiting.")
        # Re-raise any exception that occurred during collection
        if collection_exception:
            raise collection_exception[0]

    # Return 0 if all tests passed and no teardowns failed
    # Return 1 if any test failed, errored, or any teardown failed
    has_test_failures = any(
        isinstance(result.result, (FailedResult, ErrorResult))
        for result in test_results
    )
    has_fixture_teardown_failures = any(
        result.fixture_teardown_failures for result in test_results
    )
    has_session_teardown_failures = len(session_teardown_failures) > 0

    return (
        1
        if (
            has_test_failures
            or has_fixture_teardown_failures
            or has_session_teardown_failures
        )
        else 0
    )


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
