import asyncio
import logging
import sys
import threading

from snektest.collection import TestsQueue, load_tests_from_filters
from snektest.execution import run_tests
from snektest.models import (
    ArgsError,
    BadRequestError,
    CollectionError,
    FailedResult,
    FilterItem,
    UnreachableError,
)
from snektest.presenter import print_error


async def run_script() -> int:
    """Parse arguments and run tests."""
    logging_level = logging.WARNING
    potential_filter: list[str] = []
    capture_output = True
    for command in sys.argv[1:]:
        if command.startswith("-"):
            match command:
                case "-v":
                    logging_level = logging.INFO
                case "-vv":
                    logging_level = logging.DEBUG
                case "-s":
                    capture_output = False
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
    queue = TestsQueue()
    producer_thread = threading.Thread(
        target=load_tests_from_filters,
        kwargs={
            "filter_items": filter_items,
            "queue": queue,
            "loop": asyncio.get_running_loop(),
            "logger": logger,
        },
    )
    producer_thread.start()
    try:
        test_results, session_teardown_failures = await run_tests(
            queue=queue, logger=logger, capture_output=capture_output
        )
    except asyncio.CancelledError:
        logger.info("Execution stopped")
        return 2
    finally:
        producer_thread.join()
        logger.info("Producer thread ended. Exiting.")

    # Return 0 if all tests passed and no teardowns failed
    # Return 1 if any test failed or any teardown failed
    has_test_failures = any(
        isinstance(result.result, FailedResult) for result in test_results
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
