import asyncio
import logging
import pdb  # noqa: T100
import sys
import time
from collections.abc import Callable
from inspect import isasyncgen, iscoroutine, isgenerator
from pathlib import Path
from types import TracebackType

from snektest.annotations import Coroutine
from snektest.collection import TestsQueue
from snektest.fixtures import (
    get_active_function_fixtures,
    get_registered_session_fixtures,
)
from snektest.models import (
    AssertionFailure,
    BadRequestError,
    ErrorResult,
    FailedResult,
    PassedResult,
    TeardownFailure,
    TestName,
    TestResult,
    UnreachableError,
)
from snektest.output import maybe_capture_output
from snektest.presenter import print_failures, print_summary, print_test_result
from snektest.utils import get_test_function_params


async def teardown_fixture(
    fixture_name: str, generator: object
) -> TeardownFailure | None:
    """Teardown a single fixture and return failure if it occurs.

    Raises:
        BadRequestError: If the fixture function had more than one `yield`
        UnreachableError: Error that theoretically can't be reached
    """
    try:
        if isasyncgen(generator):
            await anext(generator)
        elif isgenerator(generator):
            next(generator)
    except StopAsyncIteration, StopIteration:
        return None
    except Exception:
        exc_type, exc_value, traceback = sys.exc_info()
        if exc_type is None or exc_value is None or traceback is None:
            msg = "Invalid exception info gathered during teardown. This shouldn't be possible!"
            raise UnreachableError(msg) from None
        return TeardownFailure(
            fixture_name=fixture_name,
            exc_type=exc_type,
            exc_value=exc_value,
            traceback=traceback,
        )
    else:
        msg = f"Incorrect fixture function {fixture_name} yielded more than once"
        raise BadRequestError(msg)


async def execute_test(
    name: TestName,
    func: Callable[..., Coroutine[None] | None],
    *,
    capture_output: bool = True,
) -> TestResult:
    """Execute a single test function with fixtures and output capture.

    Raises:
        UnreachableError: Error that theoretically can't be reached
    """
    with maybe_capture_output(capture_output) as (output_buffer, captured_warnings):
        param_values = ()
        if name.params_part:
            param_values = [
                param.value
                for param in get_test_function_params(func)[name.params_part]
            ]
        test_start = time.monotonic()
        try:
            res = func(*param_values)
            if iscoroutine(res):
                await res
            duration = time.monotonic() - test_start
            result = PassedResult()
        except AssertionFailure:
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
        except Exception:
            duration = time.monotonic() - test_start
            exc_type, exc_value, traceback = sys.exc_info()
            if exc_type is None or exc_value is None or traceback is None:
                msg = "Invalid exception info gathered. This shouldn't be possible!"
                raise UnreachableError(msg) from None
            result = ErrorResult(
                exc_type=exc_type,
                exc_value=exc_value,
                traceback=traceback,
            )

    # Teardown function fixtures and track failures
    with maybe_capture_output(capture_output) as (
        fixture_teardown_buffer,
        _,
    ):
        fixture_teardown_failures: list[TeardownFailure] = []
        for fixture_name, generator in get_active_function_fixtures():
            failure = await teardown_fixture(fixture_name, generator)
            if failure:
                fixture_teardown_failures.append(failure)

    fixture_teardown_output_value = fixture_teardown_buffer.getvalue() or None

    return TestResult(
        name=name,
        duration=duration,
        result=result,
        captured_output=output_buffer,
        fixture_teardown_failures=fixture_teardown_failures,
        fixture_teardown_output=fixture_teardown_output_value,
        warnings=captured_warnings,
    )


async def teardown_session_fixtures(
    *, capture_output: bool
) -> tuple[list[TeardownFailure], str | None]:
    """Teardown all session fixtures and return failures and output."""
    with maybe_capture_output(capture_output) as (teardown_output, _):
        session_teardown_failures: list[TeardownFailure] = []
        for fixture_func, (gen, _) in reversed(
            get_registered_session_fixtures().items()
        ):
            if gen is not None:
                fixture_name = fixture_func.co_name
                failure = await teardown_fixture(fixture_name, gen)
                if failure:
                    session_teardown_failures.append(failure)

    output_value = teardown_output.getvalue() or None
    return session_teardown_failures, output_value


def has_any_failures(
    test_results: list[TestResult], session_teardown_failures: list[TeardownFailure]
) -> tuple[bool, bool, bool]:
    """Check for test failures, fixture failures, and session failures."""
    has_test_failures = any(
        isinstance(result.result, (FailedResult, ErrorResult))
        for result in test_results
    )
    has_fixture_teardown_failures = any(
        result.fixture_teardown_failures for result in test_results
    )
    has_session_teardown_failures = len(session_teardown_failures) > 0
    return (
        has_test_failures,
        has_fixture_teardown_failures,
        has_session_teardown_failures,
    )


def _resolve_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except FileNotFoundError:
        return path


def _trim_traceback(
    traceback: TracebackType, *, stop_at: TracebackType
) -> TracebackType:
    frames: list[TracebackType] = []
    current = traceback
    while current is not None:
        frames.append(current)
        if current is stop_at:
            break
        current = current.tb_next
    new_traceback: TracebackType | None = None
    for frame in reversed(frames):
        new_traceback = TracebackType(
            new_traceback, frame.tb_frame, frame.tb_lasti, frame.tb_lineno
        )
    return new_traceback or traceback


def _traceback_for_file(
    traceback: TracebackType, *, preferred_file: Path | None
) -> TracebackType:
    preferred = _resolve_path(preferred_file)
    if preferred is None:
        return traceback

    selected: TracebackType | None = None
    current = traceback
    while current is not None:
        frame_path = Path(current.tb_frame.f_code.co_filename)
        resolved = _resolve_path(frame_path)
        if resolved == preferred:
            selected = current
        current = current.tb_next
    if selected is None:
        return traceback
    return _trim_traceback(traceback, stop_at=selected)


def _maybe_debug_test_result(test_result: TestResult, *, pdb_on_failure: bool) -> bool:
    if not pdb_on_failure:
        return False
    if isinstance(test_result.result, (FailedResult, ErrorResult)):
        traceback = _traceback_for_file(
            test_result.result.traceback, preferred_file=test_result.name.file_path
        )
        pdb.post_mortem(traceback)
        return True
    if test_result.fixture_teardown_failures:
        traceback = _traceback_for_file(
            test_result.fixture_teardown_failures[0].traceback,
            preferred_file=test_result.name.file_path,
        )
        pdb.post_mortem(traceback)
        return True
    return False


def _maybe_debug_session_teardown(
    session_teardown_failures: list[TeardownFailure], *, pdb_on_failure: bool
) -> bool:
    if not pdb_on_failure or not session_teardown_failures:
        return False
    pdb.post_mortem(session_teardown_failures[0].traceback)
    return True


async def run_tests(
    queue: TestsQueue,
    *,
    logger: logging.Logger,
    capture_output: bool = True,
    pdb_on_failure: bool = False,
) -> tuple[list[TestResult], list[TeardownFailure]]:
    """Run all tests from the queue and handle session fixture teardown."""
    total_duration = time.monotonic()
    test_results: list[TestResult] = []
    pdb_triggered = False
    try:
        while True:
            name, func = await queue.get()
            logger.info("Processing item %s", name)
            test_result = await execute_test(name, func, capture_output=capture_output)
            test_results.append(test_result)
            print_test_result(test_result)
            if not pdb_triggered and _maybe_debug_test_result(
                test_result, pdb_on_failure=pdb_on_failure
            ):
                pdb_triggered = True
                break
    except asyncio.QueueShutDown:
        pass
    finally:
        session_teardown_failures, session_output = await teardown_session_fixtures(
            capture_output=capture_output
        )
        if not pdb_triggered and _maybe_debug_session_teardown(
            session_teardown_failures, pdb_on_failure=pdb_on_failure
        ):
            pdb_triggered = True

        # Determine if we should show session teardown output
        (
            has_test_failures,
            has_fixture_teardown_failures,
            has_session_teardown_failures,
        ) = has_any_failures(test_results, session_teardown_failures)

        session_output_for_display = None
        if session_output and (
            has_test_failures
            or has_fixture_teardown_failures
            or has_session_teardown_failures
        ):
            session_output_for_display = session_output

        print_failures(
            test_results,
            session_teardown_failures=session_teardown_failures,
            session_teardown_output=session_output_for_display,
        )
        print_summary(
            test_results,
            session_teardown_failures=session_teardown_failures,
            total_duration=time.monotonic() - total_duration,
        )
    return test_results, session_teardown_failures
