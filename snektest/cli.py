import asyncio
import logging
import sys
import threading
import time
from collections.abc import Callable
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getmembers, isasyncgen, iscoroutine, isfunction, isgenerator
from io import StringIO
from pathlib import Path
from sys import modules
from types import FunctionType
from typing import Any, TypeGuard

from pydantic import ValidationError

from snektest import (
    _FUNCTION_FIXTURES,  # pyright: ignore[reportPrivateUsage]
)
from snektest.annotations import Coroutine, PyFilePath, validate_PyFilePath
from snektest.models import (
    ArgsError,
    BadRequestError,
    CollectionError,
    FailedResult,
    FilterItem,
    PassedResult,
    TestName,
    TestResult,
    UnreachableError,
)
from snektest.presenter import (
    print_error,
    print_failures,
    print_summary,
    print_test_result,
)
from snektest.utils import (
    get_registered_session_fixtures,
    get_test_function_params,
    is_test_function,
)

TEST_FILE_PREFIX = "test_"

FuncTestEntry = tuple[TestName, FunctionType]
ClassTestEntry = tuple[TestName, type[Any]]

TestsQueue = asyncio.Queue[FuncTestEntry | ClassTestEntry]


def load_tests_from_file(
    file_path: PyFilePath,
    filter_item: FilterItem,
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    logger: logging.Logger,
) -> None:
    module_name = ".".join(file_path.with_suffix("").parts)
    if module_name in modules:
        module = modules[module_name]
    else:
        logger.info("Will import module: %s", module_name)
        spec = spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            msg = f"Could not load spec from {file_path}"
            raise CollectionError(msg)

        module = module_from_spec(spec)
        modules[module_name] = module
        spec.loader.exec_module(module)

    runnable_functions = [func for _, func in getmembers(module, isfunction)]
    runnable_functions = filter(is_test_function, runnable_functions)
    if filter_item.function_name:
        runnable_functions = filter(
            lambda func: func.__name__ == filter_item.function_name, runnable_functions
        )

    for func in runnable_functions:
        for param_names in get_test_function_params(func):
            if filter_item.params and filter_item.params != param_names:
                continue
            test_name = TestName(
                file_path=file_path, func_name=func.__name__, params_part=param_names
            )
            logger.info("Producing test named %s", test_name)
            _ = loop.call_soon_threadsafe(queue.put_nowait, (test_name, func))


def generate_file_list(
    filter_item: FilterItem, *, logger: logging.Logger
) -> list[PyFilePath]:
    """Generate a list of valid file paths for given filter item.
    Create a common data shape for both dir and file paths in filters"""

    def path_is_runnable(
        file_path: Path, *, logger: logging.Logger
    ) -> TypeGuard[PyFilePath]:
        if not file_path.name.startswith(TEST_FILE_PREFIX):
            logger.debug("Skipping non-test python file %s", file_path)
            return False
        try:
            file_path = validate_PyFilePath(file_path)
        except ValidationError:
            logger.debug("Skipping non-python file %s", file_path)
            return False
        return True

    if filter_item.file_path.is_dir():
        paths = [
            dirpath / name
            for dirpath, _, filenames in filter_item.file_path.walk()
            for name in filenames
        ]
    else:
        paths = [filter_item.file_path]

    return [path for path in paths if path_is_runnable(path, logger=logger)]


def load_tests_from_filters(
    filter_items: list[FilterItem],
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    logger: logging.Logger,
) -> None:
    logger.info("Test collector started")
    for filter_item in filter_items:
        file_paths = generate_file_list(filter_item, logger=logger)
        if len(file_paths) == 0:
            logger.debug("Filter item %s had no runnable files", filter_item)
        for file_path in file_paths:
            load_tests_from_file(
                file_path=file_path,
                filter_item=filter_item,
                queue=queue,
                loop=loop,
                logger=logger,
            )

    _ = loop.call_soon_threadsafe(queue.shutdown)


async def execute_test(
    name: TestName, func: Callable[..., Coroutine[None] | None]
) -> TestResult:
    output_buffer = StringIO()
    system_stdout = sys.stdout
    system_stderr = sys.stderr
    sys.stdout = output_buffer
    sys.stderr = output_buffer
    param_values = ()
    if name.params_part:
        param_values = [
            param.value for param in get_test_function_params(func)[name.params_part]
        ]
    test_start = time.monotonic()
    try:
        res = func(*param_values)
        if iscoroutine(res):
            await res
        duration = time.monotonic() - test_start
        result = PassedResult()
    except Exception:
        duration = time.monotonic() - test_start
        exc_type, exc_value, traceback = sys.exc_info()
        if exc_type is None or exc_value is None or traceback is None:
            msg = "Invalid exception info gathered. This shouldn't be possible!"
            raise UnreachableError(msg) from None
        result = FailedResult(
            exc_type=exc_type,
            exc_value=exc_value,
            traceback=traceback,
        )
    # TODO: report teardown failure separately
    for generator in reversed(_FUNCTION_FIXTURES):
        try:
            if isasyncgen(generator):
                await anext(generator)
            elif isgenerator(generator):
                next(generator)
            else:
                msg = "Is there no better way"
                raise UnreachableError(msg)
        except StopAsyncIteration, StopIteration:
            pass
        else:
            # TODO: if there's multiple generators for a fixture, we should mention which one had the problem
            msg = f"Incorrect fixture function {generator.ag_code.co_name}"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            raise BadRequestError(msg)
    _FUNCTION_FIXTURES.clear()
    sys.stdout = system_stdout
    sys.stderr = system_stderr
    return TestResult(
        name=name, duration=duration, result=result, captured_output=output_buffer
    )


async def run_tests(queue: TestsQueue, *, logger: logging.Logger) -> list[TestResult]:
    total_duration = time.monotonic()
    test_results: list[TestResult] = []
    try:
        while True:
            name, func = await queue.get()
            logger.info("Processing item %s", name)
            test_results.append(await execute_test(name, func))
            print_test_result(test_results[-1])
    except asyncio.QueueShutDown:
        pass
    finally:
        # TODO: this could be done with a context manager
        system_stdout = sys.stdout
        system_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        session_teardown_error: BadRequestError | None = None
        for fixture_func, (gen, _) in reversed(
            get_registered_session_fixtures().items()
        ):
            if gen is not None:
                try:
                    if isasyncgen(gen):
                        await anext(gen)
                    elif isgenerator(gen):
                        next(gen)
                    else:
                        msg = "I should stop doing this"
                        raise UnreachableError(msg)
                except StopAsyncIteration, StopIteration:
                    pass
                else:
                    # TODO: don't stop at the first error
                    msg = f"Fixture function {fixture_func} more than one yield"
                    session_teardown_error = BadRequestError(msg)
        sys.stdout = system_stdout
        sys.stderr = system_stderr
        print_failures(test_results)
        print_summary(test_results, total_duration=time.monotonic() - total_duration)
        if session_teardown_error:
            raise session_teardown_error
    return test_results


async def run_script() -> int:
    logging_level = logging.WARNING
    potential_filter: list[str] = []
    for command in sys.argv[1:]:
        if command.startswith("-"):
            match command:
                case "-v":
                    logging_level = logging.INFO
                case "-vv":
                    logging_level = logging.DEBUG
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
        test_results = await run_tests(queue=queue, logger=logger)
    except asyncio.CancelledError:
        logger.info("Execution stopped")
        return 2
    finally:
        producer_thread.join()
        logger.info("Producer thread ended. Exiting.")

    # Return 0 if all tests passed, 1 if any test failed
    has_failures = any(isinstance(result.result, FailedResult) for result in test_results)
    return 1 if has_failures else 0


def main() -> None:
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
