import asyncio
import logging
import sys
import threading
import time
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getmembers, isfunction
from pathlib import Path
from sys import modules
from types import FunctionType
from typing import Any, TypeGuard

from pydantic import ValidationError

from snektest import _FUNCTION_FIXTURES  # pyright: ignore[reportPrivateUsage]
from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.models import (
    FailedResult,
    FilterItem,
    PassedResult,
    TestName,
    TestResult,
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


# TODO: this should just return test names, not do the queue call
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
            raise ImportError(msg)

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
            # TODO: this could be made cleaner
            if filter_item.params and ", ".join(filter_item.params) != ", ".join(
                param_names
            ):
                continue
            test_name = TestName(
                file_path=file_path, func_name=func.__name__, param_names=param_names
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


async def execute_stuff(queue: TestsQueue, *, logger: logging.Logger) -> None:  # noqa: C901, PLR0912
    total_duration = time.monotonic()
    try:
        while True:
            name, func = await queue.get()
            logger.info("Processing item %s", name)
            params = ()
            if name.param_names:
                params = [
                    param.value
                    for param in get_test_function_params(func)[name.param_names]
                ]
            test_start = time.monotonic()
            try:
                # TODO: capture output
                await func(*params)
                duration = time.monotonic() - test_start
                result = PassedResult()
            except Exception:
                duration = time.monotonic() - test_start
                exc_type, exc_value, traceback = sys.exc_info()
                if exc_type is None or exc_value is None or traceback is None:
                    msg = "Is this even possible?"
                    raise RuntimeError(msg) from None
                result = FailedResult(
                    exc_type=exc_type,
                    exc_value=exc_value,
                    traceback=traceback,
                )
            print_test_result(TestResult(name=name, duration=duration, result=result))
            # TODO: report teardown failure separately
            for generator in reversed(_FUNCTION_FIXTURES):
                try:
                    await anext(generator)
                except StopAsyncIteration:
                    pass
                else:
                    # TODO: if there's multiple generators for a fixture, we should mention which one had the problem
                    msg = f"Incorrect fixture function {generator.ag_code.co_name}"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    raise ValueError(msg)
            _FUNCTION_FIXTURES.clear()
    except asyncio.QueueShutDown:
        pass
    finally:
        for fixture_func, (gen, _) in reversed(
            get_registered_session_fixtures().items()
        ):
            if gen is not None:
                try:
                    await anext(gen)
                except StopAsyncIteration:
                    pass
                else:
                    msg = f"Incorrect fixture function {fixture_func}"
                    raise ValueError(msg)
        # TODO: I don't really like this way of working with the presenter.
        # It feels like spooky action at a distance
        print_failures()
        print_summary(time.monotonic() - total_duration)


async def main() -> None:
    logging_level = logging.WARNING
    potential_filter: list[str] = []
    for command in sys.argv[1:]:
        if command.startswith("-"):
            match command:
                case "-v":
                    logging_level = logging.INFO
                case "--v":
                    logging_level = logging.DEBUG
                case _:
                    # TODO: should raise a proper error. Also, use rich to print this in red I guess.
                    print_error(f"Invalid option: `{command}`")
                    return
        else:
            potential_filter.append(command)
    if not potential_filter:
        potential_filter.append(".")
    logging.basicConfig(level=logging_level)
    logger = logging.getLogger("snektest")

    try:
        filter_items = [FilterItem(item) for item in potential_filter]
    except ValueError as e:
        print_error(str(e))
        # TODO: don't simply return
        return
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
        await execute_stuff(queue=queue, logger=logger)
    except asyncio.CancelledError:
        logger.info("Execution stopped")
    finally:
        # TODO: should make the function be able to exit early
        producer_thread.join()
        logger.info("Producer thread ended. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())
