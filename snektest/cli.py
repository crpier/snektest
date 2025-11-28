import asyncio
import logging
import sys
import threading
import time
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getmembers, isfunction
from sys import modules
from types import FunctionType
from typing import Any

from pydantic import ValidationError

from snektest.annotations import PyFilePath, validate_PyFilePath
from snektest.models import (
    FailedResult,
    FilterItem,
    PassedResult,
    TestName,
    TestResult,
)
from snektest.presenter import print_failures, print_summary, print_test_result
from snektest.utils import (
    get_loaded_function_fixtures,
    get_registered_session_fixtures,
    get_test_function_params,
    is_test_function,
)

TEST_FILE_PREFIX = "test_"

FuncTestEntry = tuple[TestName, FunctionType]
ClassTestEntry = tuple[TestName, type[Any]]

TestsQueue = asyncio.Queue[FuncTestEntry | ClassTestEntry]

# TODO: I'm pretty sure I don't need to do this, I just need to make the
# execute_stuff function return its duration
TOTAL_DURATION = 0.0


def load_from_file(
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

    for name, func in getmembers(module, isfunction):
        if not is_test_function(func):
            continue
        if filter_item.function_name is not None and filter_item.function_name != name:
            continue
        for param_names in get_test_function_params(func):
            if filter_item.params and ", ".join(filter_item.params) != ", ".join(
                param_names
            ):
                continue
            test_name = TestName(
                file_path=file_path, func_name=name, param_names=param_names
            )
            logger.info("Producing test named %s", test_name)
            _ = loop.call_soon_threadsafe(queue.put_nowait, (test_name, func))


def load_from_filters(
    filter_items: list[FilterItem],
    queue: TestsQueue,
    loop: asyncio.AbstractEventLoop,
    *,
    logger: logging.Logger,
) -> None:
    logger.info("Test collector started")
    for filter_item in filter_items:
        if filter_item.file_path.is_dir():
            for dirpath, _, filenames in filter_item.file_path.walk():
                for name in filenames:
                    if not name.startswith(TEST_FILE_PREFIX):
                        logger.debug("Skipping non-test python file %s", dirpath / name)
                        continue
                    try:
                        file_path = validate_PyFilePath(dirpath / name)
                    except ValidationError:
                        logger.debug("Skipping non-python file %s", dirpath / name)
                        continue
                    load_from_file(
                        file_path=file_path,
                        filter_item=filter_item,
                        queue=queue,
                        loop=loop,
                        logger=logger,
                    )
        else:
            if not filter_item.file_path.name.startswith(TEST_FILE_PREFIX):
                logger.debug("Skipping non-test python file %s", filter_item.file_path)
                continue
            try:
                file_path = validate_PyFilePath(filter_item.file_path)
            except ValidationError:
                logger.debug("Skipping non-python file %s", filter_item.file_path)
                continue
            load_from_file(
                file_path=file_path,
                filter_item=filter_item,
                queue=queue,
                loop=loop,
                logger=logger,
            )
    _ = loop.call_soon_threadsafe(queue.shutdown)


async def execute_stuff(queue: TestsQueue, *, logger: logging.Logger) -> None:  # noqa: C901, PLR0912
    try:
        while True:
            global TOTAL_DURATION  # noqa: PLW0603
            TOTAL_DURATION = time.monotonic()  # pyright: ignore[reportConstantRedefinition]
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
                    # TODO: what to put in the message?
                    message="fuck you",
                    exc_type=exc_type,
                    exc_value=exc_value,
                    traceback=traceback,
                )
            print_test_result(TestResult(name=name, duration=duration, result=result))
            # TODO: report teardown failure separately
            # TODO: we should iterate through fixtures in the reverse order of their loading
            for fixture, generators in get_loaded_function_fixtures(func).items():
                for generator in generators:
                    try:
                        await anext(generator)
                    except StopAsyncIteration:
                        pass
                    else:
                        # TODO: if there's multiple generators for a fixture, we should mention which one had the problem
                        msg = f"Incorrect fixture function {fixture}"
                        raise ValueError(msg)
    except asyncio.QueueShutDown:
        for fixture_func, (gen, _) in get_registered_session_fixtures().items():
            if gen is not None:
                try:
                    await anext(gen)
                except StopAsyncIteration:
                    pass
                else:
                    msg = f"Incorrect fixture function {fixture_func}"
                    raise ValueError(msg)


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
                    print(f"Invalid command: {command}")
        else:
            try:
                potential_filter.append(command)
            except ValueError as e:
                # TODO: use `rich` to color the error in red
                print(e)
                # TODO: is there a better option than simple return? We should at least set the exit code
                return
    logging.basicConfig(level=logging_level)
    logger = logging.getLogger("snektest")

    filter_items = [FilterItem(item) for item in potential_filter]
    logger.info("Filters=%s", filter_items)
    queue = TestsQueue()
    producer_thread = threading.Thread(
        target=load_from_filters,
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
        # TODO: I don't really like this way of working with the presenter.
        # It feels like spooky action at a distance
        print_failures()
        print_summary(time.monotonic() - TOTAL_DURATION)
        logger.info("Execution stopped")
    finally:
        # TODO: should make the function be able to exit early
        producer_thread.join()
        logger.info("Producer thread ended. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())
