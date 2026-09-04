"""Test execution, fixture teardown, debugging, and run orchestration."""

import asyncio
import pdb  # noqa: T100
import sys
import time
from collections.abc import Callable, Coroutine, Generator, Sequence
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import replace
from inspect import iscoroutine
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from snektest.benchmark import collect_benchmarks
from snektest.benchmark_baseline import BenchmarkBaseline
from snektest.diagnostics import (
    LiveDiagnosticStore,
    snapshot_exception,
    use_live_diagnostic_store,
)
from snektest.fixtures import FixtureRegistry, current_registry, use_registry
from snektest.memory import collect_measurements
from snektest.models import (
    DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    AssertionFailure,
    BackgroundFailure,
    BadRequestError,
    ErrorResult,
    ExpectedFailureResult,
    FailedResult,
    PassedResult,
    RunResult,
    RunTeardownDiagnostics,
    SkippedResult,
    TeardownFailure,
    TestCase,
    TestResult,
    TestTimeoutError,
    UnexpectedPassResult,
    UnreachableError,
    _ExpectedFailureSignal,
    _SkipSignal,
)
from snektest.output import maybe_capture_output
from snektest.reporting import ConsoleRunReporter, RunReporter, result_for_retention
from snektest.task_cleanup import TaskCleanup, cancel_tasks
from snektest.thread_observation import observe_background_failures

_test_task_owner: ContextVar[object | None] = ContextVar(
    "snektest_test_task_owner", default=None
)


@contextmanager
def _test_task_scope(owner: object) -> Generator[None]:
    """Tag child tasks with the test whose execution context created them."""
    token = _test_task_owner.set(owner)
    try:
        yield
    finally:
        _test_task_owner.reset(token)


async def _await_test_body(
    coro: Coroutine[Any, Any, object],
    timeout: float | None,  # noqa: ASYNC109
) -> None:
    """Await an async test body, optionally bounding it with a timeout.

    The timeout only fires while the body is suspended on an `await`; a test that
    never yields to the loop (sync work, or a CPU-bound coroutine) cannot be
    interrupted. A fired timeout raises `TestTimeoutError`; a `TimeoutError` the test
    raised itself is left to propagate unchanged.
    """
    if timeout is None:
        await coro
        return
    cancel_scope = asyncio.timeout(timeout)
    try:
        async with cancel_scope:
            await coro
    except TimeoutError:
        if cancel_scope.expired():
            raise TestTimeoutError(timeout) from None
        raise


async def _cancel_pending_test_tasks(
    owner: object,
    registry: FixtureRegistry,
    cleanup_timeout: float,
) -> TaskCleanup:
    """Cancel tasks owned by one test after its fixtures have torn down."""
    return await cancel_tasks(
        {
            task
            for task in asyncio.all_tasks()
            if task.get_context().get(_test_task_owner) is owner
            and not task.done()
            and not registry.owns_task(task)
        },
        timeout=cleanup_timeout,
    )


def _task_leak_result(
    leaked_task_count: int,
    outcome_result: (
        PassedResult | SkippedResult | ExpectedFailureResult | UnexpectedPassResult
    ),
    *,
    cleanup_timeout: float,
    resistant_task_count: int,
) -> FailedResult:
    """Build the failure reported after fixture teardown leaves tasks pending."""
    task_word = "task" if leaked_task_count == 1 else "tasks"
    message = f"async test leaked {leaked_task_count} pending {task_word}"
    if resistant_task_count:
        message += (
            f"; {resistant_task_count} resisted cancellation for {cleanup_timeout:g}s"
        )
    try:
        raise AssertionFailure(message)  # noqa: TRY301
    except AssertionFailure as error:
        traceback = error.__traceback__
        if traceback is None:
            msg = "Task leak failure had no traceback. This shouldn't be possible!"
            raise UnreachableError(msg) from None
        return FailedResult(
            exception=snapshot_exception(type(error), error, traceback),
            benchmarks=outcome_result.benchmarks,
            benchmark_comparisons=outcome_result.benchmark_comparisons,
        )


def _with_background_failures(
    test_result: TestResult,
    failures: list[BackgroundFailure],
) -> TestResult:
    if not failures:
        return test_result
    if isinstance(test_result.result, (FailedResult, ErrorResult)):
        return replace(test_result, background_failures=tuple(failures))

    primary = next(
        (failure for failure in failures if failure.origin != "thread_leak"),
        failures[0],
    )
    outcome = test_result.result
    if primary.origin == "thread_leak":
        result: FailedResult | ErrorResult = FailedResult(
            exception=primary.exception,
            benchmarks=outcome.benchmarks,
            benchmark_comparisons=outcome.benchmark_comparisons,
        )
    else:
        result = ErrorResult(
            exception=primary.exception,
            benchmarks=outcome.benchmarks,
            benchmark_comparisons=outcome.benchmark_comparisons,
        )
    return replace(
        test_result,
        result=result,
        background_failures=tuple(failures),
    )


async def execute_test(
    test_case: TestCase,
    *,
    capture_output: bool = True,
    timeout: float | None = None,  # noqa: ASYNC109
    benchmark_baseline: BenchmarkBaseline | None = None,
    exc_info_provider: Callable[
        [], tuple[object | None, object | None, TracebackType | None]
    ] = sys.exc_info,
) -> TestResult:
    """Execute one test while observing failures outside its call stack."""
    with observe_background_failures() as background_failures:
        test_result = await _execute_test(
            test_case,
            capture_output=capture_output,
            timeout=timeout,
            benchmark_baseline=benchmark_baseline,
            exc_info_provider=exc_info_provider,
        )
    return _with_background_failures(test_result, background_failures)


async def _execute_test(  # noqa: C901, PLR0912, PLR0915
    test_case: TestCase,
    *,
    capture_output: bool = True,
    timeout: float | None = None,  # noqa: ASYNC109
    benchmark_baseline: BenchmarkBaseline | None = None,
    exc_info_provider: Callable[
        [], tuple[object | None, object | None, TracebackType | None]
    ] = sys.exc_info,
) -> TestResult:
    """Execute a collected test case with fixtures and output capture."""
    compare_benchmark = (
        benchmark_baseline.comparator(test_case.name)
        if benchmark_baseline is not None
        else None
    )
    bad_request: BadRequestError | None = None
    interruption: BaseException | None = None
    result: (
        PassedResult
        | SkippedResult
        | ExpectedFailureResult
        | UnexpectedPassResult
        | FailedResult
        | ErrorResult
        | None
    ) = None
    registry = current_registry()
    test_task_owner = object()
    with (
        _test_task_scope(test_task_owner),
        maybe_capture_output(capture_output) as (output_buffer, captured_warnings),
        collect_benchmarks(compare=compare_benchmark) as benchmark_capture,
        collect_measurements() as measurements,
    ):
        test_start = time.monotonic()
        try:
            res = test_case.call()
            if iscoroutine(res):
                await _await_test_body(res, timeout)
            duration = time.monotonic() - test_start
            if test_case.expected_failure_reason is None:
                result = PassedResult(
                    measurements=tuple(measurements),
                    benchmarks=tuple(benchmark_capture.measurements),
                    benchmark_comparisons=tuple(benchmark_capture.comparisons),
                )
            else:
                result = UnexpectedPassResult(
                    reason=test_case.expected_failure_reason,
                    benchmarks=tuple(benchmark_capture.measurements),
                    benchmark_comparisons=tuple(benchmark_capture.comparisons),
                )
        except _ExpectedFailureSignal as expected_failure:
            duration = time.monotonic() - test_start
            result = ExpectedFailureResult(
                reason=expected_failure.reason,
                benchmarks=tuple(benchmark_capture.measurements),
                benchmark_comparisons=tuple(benchmark_capture.comparisons),
            )
        except _SkipSignal as skipped:
            duration = time.monotonic() - test_start
            result = SkippedResult(
                reason=skipped.reason,
                benchmarks=tuple(benchmark_capture.measurements),
                benchmark_comparisons=tuple(benchmark_capture.comparisons),
            )
        except AssertionFailure:
            duration = time.monotonic() - test_start
            exc_type, exc_value, traceback = exc_info_provider()
            if exc_type is None or exc_value is None or traceback is None:
                msg = "Invalid exception info gathered. This shouldn't be possible!"
                raise UnreachableError(msg) from None
            diagnostic = snapshot_exception(
                cast("type[BaseException]", exc_type),
                cast("BaseException", exc_value),
                traceback,
            )
            if test_case.expected_failure_reason is None:
                result = FailedResult(
                    exception=diagnostic,
                    benchmarks=tuple(benchmark_capture.measurements),
                    benchmark_comparisons=tuple(benchmark_capture.comparisons),
                )
            else:
                result = ExpectedFailureResult(
                    reason=test_case.expected_failure_reason,
                    exception=diagnostic,
                    benchmarks=tuple(benchmark_capture.measurements),
                    benchmark_comparisons=tuple(benchmark_capture.comparisons),
                )
        except asyncio.CancelledError as error:
            duration = time.monotonic() - test_start
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                interruption = error
            else:
                exc_type, exc_value, traceback = exc_info_provider()
                if exc_type is None or exc_value is None or traceback is None:
                    msg = "Invalid exception info gathered. This shouldn't be possible!"
                    raise UnreachableError(msg) from None
                result = FailedResult(
                    exception=snapshot_exception(
                        cast("type[BaseException]", exc_type),
                        cast("BaseException", exc_value),
                        traceback,
                    ),
                    benchmarks=tuple(benchmark_capture.measurements),
                    benchmark_comparisons=tuple(benchmark_capture.comparisons),
                )
        except BadRequestError as error:
            duration = time.monotonic() - test_start
            bad_request = error
        except Exception:
            duration = time.monotonic() - test_start
            exc_type, exc_value, traceback = exc_info_provider()
            if exc_type is None or exc_value is None or traceback is None:
                msg = "Invalid exception info gathered. This shouldn't be possible!"
                raise UnreachableError(msg) from None
            result = ErrorResult(
                exception=snapshot_exception(
                    cast("type[BaseException]", exc_type),
                    cast("BaseException", exc_value),
                    traceback,
                ),
                benchmarks=tuple(benchmark_capture.measurements),
                benchmark_comparisons=tuple(benchmark_capture.comparisons),
            )
        except BaseException as error:
            duration = time.monotonic() - test_start
            interruption = error

    with (
        _test_task_scope(test_task_owner),
        maybe_capture_output(capture_output) as (
            fixture_teardown_buffer,
            fixture_teardown_warnings,
        ),
    ):
        fixture_teardown_failures = await registry.teardown_function_fixtures(
            cleanup_timeout=timeout
        )

    fixture_teardown_output_value = fixture_teardown_buffer.getvalue() or None

    cleanup_timeout = DEFAULT_CLEANUP_TIMEOUT_SECONDS if timeout is None else timeout
    task_cleanup = await _cancel_pending_test_tasks(
        test_task_owner, registry, cleanup_timeout
    )
    if (
        result is not None
        and not isinstance(result, (FailedResult, ErrorResult))
        and task_cleanup.total
    ):
        result = _task_leak_result(
            task_cleanup.total,
            result,
            cleanup_timeout=cleanup_timeout,
            resistant_task_count=task_cleanup.resistant,
        )

    if interruption is not None:
        raise interruption
    if bad_request is not None and fixture_teardown_failures:
        traceback = bad_request.__traceback__
        if traceback is None:
            msg = "Baseline configuration error had no traceback. This shouldn't be possible!"
            raise UnreachableError(msg)
        result = ErrorResult(
            exception=snapshot_exception(
                type(bad_request),
                bad_request,
                traceback,
            ),
            benchmarks=tuple(benchmark_capture.measurements),
            benchmark_comparisons=tuple(benchmark_capture.comparisons),
        )
    elif bad_request is not None:
        raise bad_request
    if result is None:
        msg = "Test execution completed without a result. This shouldn't be possible!"
        raise UnreachableError(msg)

    return TestResult(
        name=test_case.name,
        duration=duration,
        result=result,
        markers=test_case.markers,
        captured_output=output_buffer.getvalue(),
        fixture_teardown_failures=tuple(fixture_teardown_failures),
        fixture_teardown_output=fixture_teardown_output_value,
        ordinal=test_case.ordinal,
        warnings=(*captured_warnings, *fixture_teardown_warnings),
    )


async def teardown_session_fixtures(
    *, capture_output: bool, cleanup_timeout: float | None = None
) -> tuple[list[TeardownFailure], str | None, tuple[str, ...]]:
    """Teardown all session fixtures and return failures and output."""
    with maybe_capture_output(capture_output) as (
        teardown_output,
        teardown_warnings,
    ):
        session_teardown_failures = await current_registry().teardown_session_fixtures(
            cleanup_timeout=cleanup_timeout
        )

    output_value = teardown_output.getvalue() or None
    return session_teardown_failures, output_value, tuple(teardown_warnings)


async def teardown_run_fixtures(
    *, capture_output: bool, cleanup_timeout: float | None = None
) -> tuple[list[TeardownFailure], str | None, tuple[str, ...]]:
    """Tear down host-owned run fixtures and return failures and output."""
    with maybe_capture_output(capture_output) as (
        teardown_output,
        teardown_warnings,
    ):
        failures = await current_registry().teardown_run_fixtures(
            cleanup_timeout=cleanup_timeout
        )
    return failures, teardown_output.getvalue() or None, tuple(teardown_warnings)


def has_any_failures(
    test_results: list[TestResult],
    session_teardown_failures: list[TeardownFailure],
    run_teardown_failures: list[TeardownFailure] | None = None,
) -> tuple[bool, bool, bool, bool]:
    """Check for test failures, fixture failures, and session failures."""
    has_test_failures = any(
        isinstance(result.result, (FailedResult, ErrorResult, UnexpectedPassResult))
        for result in test_results
    )
    has_fixture_teardown_failures = any(
        result.fixture_teardown_failures for result in test_results
    )
    has_session_teardown_failures = len(session_teardown_failures) > 0
    has_run_teardown_failures = bool(run_teardown_failures)
    return (
        has_test_failures,
        has_fixture_teardown_failures,
        has_session_teardown_failures,
        has_run_teardown_failures,
    )


def _resolve_path(
    path: Path | None,
    *,
    resolver: Callable[[Path], Path] = Path.resolve,
) -> Path | None:
    if path is None:
        return None
    try:
        resolved = resolver(path)
    except FileNotFoundError:
        return path
    if resolved is path:
        return resolved
    if str(resolved):
        return resolved
    return resolved


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
    if new_traceback is None:
        return traceback
    return new_traceback


def _traceback_for_file(
    traceback: TracebackType,
    *,
    preferred_file: Path | None,
    resolver: Callable[[Path], Path] = Path.resolve,
) -> TracebackType:
    preferred = _resolve_path(preferred_file, resolver=resolver)
    if preferred is None:
        return traceback

    selected: TracebackType | None = None
    current = traceback
    while current is not None:
        frame_path = Path(current.tb_frame.f_code.co_filename)
        resolved = _resolve_path(frame_path, resolver=resolver)
        if resolved == preferred:
            selected = current
        current = current.tb_next
    if selected is None:
        return traceback
    return _trim_traceback(traceback, stop_at=selected)


def _maybe_debug_test_result(
    test_result: TestResult,
    *,
    live_diagnostics: LiveDiagnosticStore,
    pdb_on_failure: bool,
    post_mortem: Callable[[TracebackType], None] = pdb.post_mortem,
    resolver: Callable[[Path], Path] = Path.resolve,
) -> bool:
    if not pdb_on_failure:
        return False
    if isinstance(test_result.result, (FailedResult, ErrorResult)):
        live_traceback = live_diagnostics.traceback_for(test_result.result.exception)
        if live_traceback is None:
            return False
        traceback = _traceback_for_file(
            live_traceback,
            preferred_file=test_result.name.file_path,
            resolver=resolver,
        )
        post_mortem(traceback)
        return True
    if test_result.fixture_teardown_failures:
        live_traceback = live_diagnostics.traceback_for(
            test_result.fixture_teardown_failures[0].exception
        )
        if live_traceback is None:
            return False
        traceback = _traceback_for_file(
            live_traceback,
            preferred_file=test_result.name.file_path,
            resolver=resolver,
        )
        post_mortem(traceback)
        return True
    return False


def _maybe_debug_session_teardown(
    session_teardown_failures: list[TeardownFailure],
    *,
    live_diagnostics: LiveDiagnosticStore,
    pdb_on_failure: bool,
    post_mortem: Callable[[TracebackType], None] = pdb.post_mortem,
) -> bool:
    if not pdb_on_failure or not session_teardown_failures:
        return False
    live_traceback = live_diagnostics.traceback_for(
        session_teardown_failures[0].exception
    )
    if live_traceback is None:
        return False
    post_mortem(live_traceback)
    return True


async def run_tests(  # noqa: PLR0913
    test_cases: Sequence[TestCase],
    *,
    capture_output: bool = True,
    collection_output: str = "",
    collection_warnings: tuple[str, ...] = (),
    pdb_on_failure: bool = False,
    timeout: float | None = None,  # noqa: ASYNC109
    post_mortem: Callable[[TracebackType], None] = pdb.post_mortem,
    reporter: RunReporter | None = None,
    resolver: Callable[[Path], Path] = Path.resolve,
    benchmark_baseline: BenchmarkBaseline | None = None,
    teardown_diagnostics: RunTeardownDiagnostics | None = None,
) -> RunResult:
    """Run a completed test plan and report one normalized completion."""
    if reporter is None:
        reporter = ConsoleRunReporter()

    total_duration = time.monotonic()
    test_results: list[TestResult] = []
    session_teardown_failures: list[TeardownFailure] = []
    run_teardown_failures: list[TeardownFailure] = []
    pdb_triggered = False
    live_diagnostics = LiveDiagnosticStore()
    diagnostic_context = (
        use_live_diagnostic_store(live_diagnostics) if pdb_on_failure else nullcontext()
    )
    with diagnostic_context, use_registry(FixtureRegistry()):
        try:
            for test_case in test_cases:
                test_result = await execute_test(
                    test_case,
                    capture_output=capture_output,
                    timeout=timeout,
                    benchmark_baseline=benchmark_baseline,
                )
                reporter.test_finished(test_result)
                test_results.append(result_for_retention(reporter, test_result))
                if not pdb_triggered and _maybe_debug_test_result(
                    test_result,
                    live_diagnostics=live_diagnostics,
                    pdb_on_failure=pdb_on_failure,
                    post_mortem=post_mortem,
                    resolver=resolver,
                ):
                    pdb_triggered = True
                    break
        finally:
            (
                session_teardown_failures,
                session_output,
                session_warnings,
            ) = await teardown_session_fixtures(
                capture_output=capture_output, cleanup_timeout=timeout
            )
            (
                run_teardown_failures,
                run_output,
                run_warnings,
            ) = await teardown_run_fixtures(
                capture_output=capture_output, cleanup_timeout=timeout
            )
            if teardown_diagnostics is not None:
                teardown_diagnostics.run_output = run_output
                teardown_diagnostics.run_warnings = run_warnings
                teardown_diagnostics.session_output = session_output
                teardown_diagnostics.session_warnings = session_warnings
            if not pdb_triggered and _maybe_debug_session_teardown(
                session_teardown_failures,
                live_diagnostics=live_diagnostics,
                pdb_on_failure=pdb_on_failure,
                post_mortem=post_mortem,
            ):
                pdb_triggered = True

            completed_run = RunResult.from_execution(
                collection_output=collection_output,
                collection_warnings=collection_warnings,
                run_teardown_failures=run_teardown_failures,
                run_teardown_output=run_output,
                run_teardown_warnings=run_warnings,
                session_teardown_failures=session_teardown_failures,
                session_teardown_output=session_output,
                session_teardown_warnings=session_warnings,
                test_results=test_results,
                total_duration=time.monotonic() - total_duration,
            )
            reporter.run_finished(completed_run)
    return completed_run
