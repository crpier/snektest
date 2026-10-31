"""Spawn-based persistent process execution and mutex-aware scheduling."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy import sys
lazy import time
lazy from collections.abc import Callable, Sequence
lazy from contextlib import suppress
lazy from dataclasses import dataclass
lazy from io import StringIO
lazy from multiprocessing import get_context
lazy from multiprocessing.context import SpawnContext
lazy from multiprocessing.process import BaseProcess
lazy from pathlib import Path
lazy from typing import Any, Literal, cast

lazy from snektest.annotations import AsyncFixture, Coroutine, Fixture
lazy from snektest.benchmark_baseline import BenchmarkBaseline
lazy from snektest.collection import collect_tests_from_filters
lazy from snektest.collection_errors import format_collection_error
lazy from snektest.decorators import RunFixtureIdentity, get_run_fixture_catalog
lazy from snektest.diagnostics import snapshot_exception
lazy from snektest.execution import execute_test, teardown_session_fixtures
lazy from snektest.fixtures import (
    FixtureRegistry,
    use_registry,
    use_run_fixture_loader,
)
lazy from snektest.models import (
    CollectionError,
    EmptyCollectionError,
    ErrorResult,
    ExceptionDiagnostic,
    FilterItem,
    InvalidTestDefinitionError,
    RetainedDirectory,
    RunInfrastructureError,
    RunResult,
    RunTeardownDiagnostics,
    TeardownFailure,
    TestCase,
    TestName,
    TestResult,
)
lazy from snektest.output import maybe_capture_output
lazy from snektest.progress import ProgressSettings, observe_process, progress_settings
lazy from snektest.reporting import RunReporter, result_for_retention
lazy from snektest.run_fixture_publication import (
    LoadRunFixture,
    ProcessConnection,
    RunFixtureFailed,
    RunFixtureLoaded,
    RunFixturePublication,
    WorkerRunFixtures,
)
lazy from snektest.type_contracts import typing_configuration


@dataclass(frozen=True)
class CaseManifest:
    """Structural case identity compared across independently importing processes."""

    expected_failure_reason: str | None
    file_path: str
    func_name: str
    markers: tuple[str, ...]
    mutex: str | None
    ordinal: int
    params_part: str


@dataclass(frozen=True)
class _BootstrapReady:
    manifest: tuple[CaseManifest, ...]
    run_fixture_catalog: tuple[RunFixtureIdentity, ...]
    collection_output: str = ""
    collection_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _BootstrapFailed:
    exception_type: str
    message: str
    diagnostic: ExceptionDiagnostic | None = None
    collection_full_traceback: str | None = None
    collection_console: str | None = None
    collection_output: str = ""
    collection_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Execute:
    ordinal: int


@dataclass(frozen=True)
class _ExecutionFinished:
    result: TestResult


@dataclass(frozen=True)
class _Shutdown: ...


@dataclass(frozen=True)
class _WorkerStopped:
    retained_directories: tuple[RetainedDirectory, ...]
    session_teardown_failures: tuple[TeardownFailure, ...]
    session_teardown_output: str | None
    session_teardown_warnings: tuple[str, ...]


@dataclass(frozen=True)
class _HostStopped:
    run_teardown_failures: tuple[TeardownFailure, ...]
    run_teardown_output: str | None
    run_teardown_warnings: tuple[str, ...]


@dataclass
class _Worker:
    """Coordinator-owned connection and lifecycle state for one process."""

    connection: ProcessConnection
    identifier: int
    process: BaseProcess
    active_ordinal: int | None = None
    waiting_for_run_fixture: bool = False


def _manifest(test_cases: Sequence[TestCase]) -> tuple[CaseManifest, ...]:
    return tuple(
        CaseManifest(
            expected_failure_reason=test_case.expected_failure_reason,
            file_path=str(test_case.name.file_path),
            func_name=test_case.name.func_name,
            markers=test_case.markers,
            mutex=test_case.mutex,
            ordinal=test_case.ordinal,
            params_part=test_case.name.params_part,
        )
        for test_case in test_cases
    )


def _collect_child_plan(
    raw_filters: tuple[str, ...],
    mark: str | None,
    keyword: str | None,
    *,
    allow_empty: bool = False,
) -> list[TestCase]:
    return collect_tests_from_filters(
        [FilterItem(raw_filter) for raw_filter in raw_filters],
        allow_empty=allow_empty,
        mark=mark,
        keyword=keyword,
    )


async def _load_async_run_fixture_payload(
    registry: FixtureRegistry,
    handle: AsyncFixture[Any],
    timeout: float | None,  # noqa: ASYNC109
) -> bytes:
    """Await host setup within the publication module's allocated budget."""
    async with asyncio.timeout(timeout):
        return await cast("Coroutine[bytes]", registry.load_run_payload(handle))


def _host_main(  # noqa: PLR0913, PLR0917
    connection: ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    keyword: str | None,
    allow_empty: bool,  # noqa: FBT001
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
) -> None:
    """Collect the canonical manifest and remain alive as the fixture-host process."""
    output = StringIO()
    warnings: list[str] = []
    try:
        with maybe_capture_output(capture_output) as (output, warnings):
            test_cases = _collect_child_plan(
                raw_filters, mark, keyword, allow_empty=allow_empty
            )
        catalog = get_run_fixture_catalog()
        connection.send(
            _BootstrapReady(
                manifest=_manifest(test_cases),
                run_fixture_catalog=tuple(sorted(catalog)),
                collection_output=output.getvalue(),
                collection_warnings=tuple(warnings),
            )
        )
    except BaseException as exc:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send(
                _BootstrapFailed(
                    type(exc).__name__,
                    str(exc),
                    diagnostic=snapshot_exception(type(exc), exc, exc.__traceback__),
                    collection_console=(
                        format_collection_error(exc)
                        if isinstance(exc, CollectionError)
                        else None
                    ),
                    collection_full_traceback=(
                        format_collection_error(exc, full_traceback=True)
                        if isinstance(exc, CollectionError)
                        else None
                    ),
                    collection_output=output.getvalue(),
                    collection_warnings=tuple(warnings),
                )
            )
        connection.close()
        return

    asyncio.run(
        _serve_host(connection, catalog, capture_output=capture_output, timeout=timeout)
    )
    connection.close()


def _host_setup_output(output: StringIO | None, warnings: Sequence[str]) -> str:
    # Publication already transports setup output, not a separate warnings field.
    # Preserve host setup warnings there instead of discarding its recorder.
    text = output.getvalue() if output is not None else ""
    return text + "".join(f"{warning}\n" for warning in warnings)


async def _serve_host(
    connection: ProcessConnection,
    catalog: dict[RunFixtureIdentity, Callable[[], Fixture[Any] | AsyncFixture[Any]]],
    *,
    capture_output: bool,
    timeout: float | None,  # noqa: ASYNC109
) -> None:
    """Keep hosted async resources running while command reception is idle."""
    registry = FixtureRegistry()
    with use_registry(registry):
        while True:
            message = await asyncio.to_thread(connection.recv)
            if isinstance(message, _Shutdown):
                with maybe_capture_output(capture_output) as (output, warnings):
                    failures = await registry.teardown_run_fixtures(
                        cleanup_timeout=timeout
                    )
                connection.send(
                    _HostStopped(
                        tuple(failures),
                        output.getvalue() or None,
                        tuple(warnings),
                    )
                )
                break
            if not isinstance(message, LoadRunFixture):
                msg = (
                    f"Fixture host received unexpected message {type(message).__name__}"
                )
                connection.send(RunFixtureFailed(("", ""), msg))
                continue
            output = None
            setup_warnings: list[str] = []
            try:
                with maybe_capture_output(capture_output) as (output, setup_warnings):
                    handle = catalog[message.identity]()
                    if isinstance(handle, AsyncFixture):
                        payload = await _load_async_run_fixture_payload(
                            registry, handle, message.setup_timeout
                        )
                    else:
                        payload = cast("bytes", registry.load_run_payload(handle))
            except BaseException as exc:
                diagnostic = snapshot_exception(type(exc), exc, exc.__traceback__)
                interrupted = isinstance(
                    exc, (SystemExit, KeyboardInterrupt, asyncio.CancelledError)
                )
                connection.send(
                    RunFixtureFailed(
                        message.identity,
                        (
                            f"Run fixture {message.identity[0]}."
                            f"{message.identity[1]} publication failed: "
                            f"{diagnostic.type_name}: {diagnostic.message}"
                        ),
                        _host_setup_output(output, setup_warnings),
                        interruption=type(exc).__name__ if interrupted else None,
                        # SystemExit accepts arbitrary objects despite its code annotation.
                        exit_code=(
                            exc.code
                            if isinstance(exc, SystemExit)
                            and isinstance(
                                getattr(exc, "code", 1), (int, str, type(None))
                            )
                            else 1
                        ),
                    )
                )
            else:
                connection.send(
                    RunFixtureLoaded(
                        message.identity,
                        _host_setup_output(output, setup_warnings),
                        payload,
                    )
                )


def _worker_main(  # noqa: PLR0913, PLR0917
    connection: ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    keyword: str | None,
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
    benchmark_baseline: BenchmarkBaseline | None,
    typing_config: Path | None,
) -> None:
    """Run a worker with interactive stdin disabled."""
    with Path(os.devnull).open() as worker_stdin:
        sys.stdin = worker_stdin
        _run_worker(
            connection,
            raw_filters,
            mark,
            keyword,
            capture_output,
            timeout,
            benchmark_baseline,
            typing_config,
        )


def _run_worker(  # noqa: PLR0913, PLR0917
    connection: ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    keyword: str | None,
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
    benchmark_baseline: BenchmarkBaseline | None,
    typing_config: Path | None,
) -> None:
    """Import one local plan and execute assigned ordinals on one event loop."""
    try:
        with maybe_capture_output(capture_output):
            # The host validates empty positional selections; workers verify its manifest.
            test_cases = _collect_child_plan(
                raw_filters, mark, keyword, allow_empty=True
            )
        connection.send(
            _BootstrapReady(
                manifest=_manifest(test_cases),
                run_fixture_catalog=tuple(sorted(get_run_fixture_catalog())),
            )
        )
    except BaseException as exc:
        connection.send(_BootstrapFailed(type(exc).__name__, str(exc)))
        connection.close()
        return

    registry = FixtureRegistry()
    run_fixture_loader = WorkerRunFixtures(connection)
    with (
        use_registry(registry),
        typing_configuration(typing_config),
        use_run_fixture_loader(run_fixture_loader),
        asyncio.Runner() as runner,
    ):
        while True:
            message = runner.run(run_fixture_loader.receive_command())
            if isinstance(message, _Shutdown):
                failures, output, warnings = runner.run(
                    teardown_session_fixtures(
                        capture_output=capture_output, cleanup_timeout=timeout
                    )
                )
                connection.send(
                    _WorkerStopped(
                        retained_directories=registry.temporary_directories.retained_session,
                        session_teardown_failures=tuple(failures),
                        session_teardown_output=output,
                        session_teardown_warnings=warnings,
                    )
                )
                break
            if not isinstance(message, _Execute):
                msg = f"Execution worker received unexpected {type(message).__name__}"
                raise RunInfrastructureError(msg)
            test_case = test_cases[message.ordinal]
            result = runner.run(
                run_fixture_loader.run_case(
                    execute_test(
                        test_case,
                        capture_output=capture_output,
                        timeout=timeout,
                        benchmark_baseline=benchmark_baseline,
                    )
                )
            )
            connection.send(_ExecutionFinished(result))
    connection.close()


def _observed_child(
    target: Callable[..., object],
    connection: ProcessConnection,
    args: tuple[object, ...],
    settings: ProgressSettings | None,
    name: str,
) -> None:
    with observe_process(settings, name):
        target(connection, *args)


def _spawn_process(
    context: SpawnContext,
    target: Callable[..., object],
    args: tuple[object, ...],
    *,
    name: str,
) -> tuple[BaseProcess, ProcessConnection]:
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=_observed_child,
        args=(target, child_connection, args, progress_settings(), name),
        daemon=False,
        name=name,
    )
    process.start()
    child_connection.close()
    return process, parent_connection


async def _receive_bootstrap(
    connection: ProcessConnection,
    *,
    child_name: str,
    collection_owner: bool = False,
    timeout: float | None,  # noqa: ASYNC109
) -> _BootstrapReady:
    try:
        if timeout is None:
            message = await asyncio.to_thread(connection.recv)
        else:
            message = await asyncio.wait_for(
                asyncio.to_thread(connection.recv),
                timeout=timeout,
            )
    except TimeoutError:
        msg = f"{child_name} did not finish bootstrap within {timeout:g}s"
        raise RunInfrastructureError(msg) from None
    except (EOFError, OSError) as exc:
        msg = f"{child_name} exited during bootstrap"
        raise RunInfrastructureError(msg) from exc
    if isinstance(message, _BootstrapFailed):
        collection_error: CollectionError | None = None
        if collection_owner and message.exception_type == "EmptyCollectionError":
            collection_error = EmptyCollectionError(message.message)
        elif (
            collection_owner and message.exception_type == "InvalidTestDefinitionError"
        ):
            collection_error = InvalidTestDefinitionError(message.message)
        elif collection_owner and message.exception_type == "CollectionError":
            collection_error = CollectionError(message.message)
        if collection_error is not None:
            collection_error.collection_diagnostic = message.diagnostic
            collection_error.collection_full_traceback = (
                message.collection_full_traceback
            )
            collection_error.collection_console = message.collection_console
            collection_error.collection_output = message.collection_output
            collection_error.collection_warnings = message.collection_warnings
            raise collection_error
        msg = (
            f"{child_name} bootstrap failed: "
            f"{message.exception_type}: {message.message}"
        )
        raise RunInfrastructureError(msg)
    if not isinstance(message, _BootstrapReady):
        msg = f"{child_name} sent invalid bootstrap message"
        raise RunInfrastructureError(msg)
    return message


async def _stop_process(process: BaseProcess, connection: ProcessConnection) -> None:
    connection.close()
    if process.is_alive():
        process.terminate()
    await asyncio.to_thread(process.join, 5)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join)


async def _start_worker(  # noqa: PLR0913
    context: SpawnContext,
    *,
    canonical_bootstrap: _BootstrapReady,
    capture_output: bool,
    benchmark_baseline: BenchmarkBaseline | None,
    identifier: int,
    mark: str | None,
    keyword: str | None,
    publication: RunFixturePublication,
    raw_filters: tuple[str, ...],
    timeout: float | None,  # noqa: ASYNC109
    typing_config: Path | None = None,
) -> _Worker:
    """Start one worker serially and require the canonical structural manifest."""
    process, connection = _spawn_process(
        context,
        _worker_main,
        (
            raw_filters,
            mark,
            keyword,
            capture_output,
            timeout,
            benchmark_baseline,
            typing_config,
        ),
        name=f"snektest-worker-{identifier + 1}",
    )
    try:
        worker_bootstrap = await _receive_bootstrap(
            connection,
            child_name=f"worker {identifier + 1}",
            timeout=timeout,
        )
    except BaseException:
        await _stop_process(process, connection)
        raise
    if (
        worker_bootstrap.manifest != canonical_bootstrap.manifest
        or worker_bootstrap.run_fixture_catalog
        != canonical_bootstrap.run_fixture_catalog
    ):
        await _stop_process(process, connection)
        msg = f"worker {identifier + 1} collected a different test manifest"
        raise RunInfrastructureError(msg)
    try:
        await publication.restore(connection, worker_name=f"worker {identifier + 1}")
    except BaseException:
        await _stop_process(process, connection)
        raise
    return _Worker(
        connection=connection,
        identifier=identifier,
        process=process,
    )


def _next_runnable_ordinal(
    pending: list[int],
    manifest: tuple[CaseManifest, ...],
    active_mutexes: set[str],
) -> int | None:
    for index, ordinal in enumerate(pending):
        mutex = manifest[ordinal].mutex
        if mutex is None or mutex not in active_mutexes:
            return pending.pop(index)
    return None


def _worker_crash_result(
    case: CaseManifest,
    *,
    exit_code: int | None,
) -> TestResult:
    message = f"execution worker exited unexpectedly with code {exit_code}"
    diagnostic = ExceptionDiagnostic(
        frames=(),
        message=message,
        qualified_type_name="snektest.models.RunInfrastructureError",
        type_name="RunInfrastructureError",
    )
    return TestResult(
        captured_output="",
        duration=0.0,
        fixture_teardown_failures=(),
        fixture_teardown_output=None,
        markers=case.markers,
        name=TestName(
            file_path=Path(case.file_path),
            func_name=case.func_name,
            params_part=case.params_part,
        ),
        ordinal=case.ordinal,
        result=ErrorResult(exception=diagnostic),
        warnings=(),
    )


async def run_tests_parallel(  # noqa: C901, PLR0912, PLR0913, PLR0915
    filter_items: list[FilterItem],
    *,
    allow_empty: bool = False,
    capture_output: bool,
    benchmark_baseline: BenchmarkBaseline | None,
    fail_fast: bool = False,
    mark: str | None,
    keyword: str | None = None,
    reporter: RunReporter,
    timeout: float | None,  # noqa: ASYNC109
    workers: int | Literal["auto"],
    teardown_diagnostics: RunTeardownDiagnostics | None = None,
    typing_config: Path | None = None,
) -> RunResult:
    """Run a canonical plan across persistent spawn workers."""
    started_at = time.monotonic()
    context = get_context("spawn")
    raw_filters = tuple(str(filter_item) for filter_item in filter_items)
    host_process, host_connection = _spawn_process(
        context,
        _host_main,
        (raw_filters, mark, keyword, allow_empty, capture_output, timeout),
        name="snektest-fixture-host",
    )
    worker_processes: list[_Worker] = []
    try:
        canonical_bootstrap = await _receive_bootstrap(
            host_connection,
            child_name="fixture host",
            collection_owner=True,
            timeout=timeout,
        )
        canonical_manifest = canonical_bootstrap.manifest
        requested_workers = (
            min(os.process_cpu_count() or 1, len(canonical_manifest))
            if workers == "auto"
            else min(workers, len(canonical_manifest))
        )
        publication = RunFixturePublication(host_connection, timeout=timeout)
        for identifier in range(requested_workers):
            worker_processes.append(  # noqa: PERF401
                await _start_worker(
                    context,
                    canonical_bootstrap=canonical_bootstrap,
                    capture_output=capture_output,
                    benchmark_baseline=benchmark_baseline,
                    typing_config=typing_config,
                    identifier=identifier,
                    mark=mark,
                    keyword=keyword,
                    publication=publication,
                    raw_filters=raw_filters,
                    timeout=timeout,
                )
            )

        pending = list(range(len(canonical_manifest)))
        active_mutexes: set[str] = set()
        receive_tasks: dict[asyncio.Task[object], _Worker] = {}
        results_by_ordinal: dict[int, TestResult] = {}
        reported_results: list[TestResult] = []
        next_report_ordinal = 0
        next_worker_identifier = requested_workers
        replacements_needed = 0
        run_ahead_limit = 1 if fail_fast else requested_workers * 2
        while pending or receive_tasks or publication.pending:
            if publication.pending and not receive_tasks:
                await publication.publish(
                    [worker.connection for worker in worker_processes]
                )
                if publication.interrupted:
                    pending.clear()
                for worker in worker_processes:
                    if not worker.waiting_for_run_fixture:
                        continue
                    worker.waiting_for_run_fixture = False
                    receive_task = asyncio.create_task(
                        asyncio.to_thread(worker.connection.recv)
                    )
                    receive_tasks[receive_task] = worker

            if replacements_needed and not receive_tasks and pending:
                for _ in range(replacements_needed):
                    worker_processes.append(
                        await _start_worker(
                            context,
                            canonical_bootstrap=canonical_bootstrap,
                            capture_output=capture_output,
                            benchmark_baseline=benchmark_baseline,
                            typing_config=typing_config,
                            identifier=next_worker_identifier,
                            mark=mark,
                            keyword=keyword,
                            publication=publication,
                            raw_filters=raw_filters,
                            timeout=timeout,
                        )
                    )
                    next_worker_identifier += 1
                replacements_needed = 0

            if not replacements_needed and not publication.pending:
                for worker in worker_processes:
                    unreported_case_count = len(results_by_ordinal) + sum(
                        active_worker.active_ordinal is not None
                        for active_worker in worker_processes
                    )
                    if unreported_case_count >= run_ahead_limit:
                        break
                    if (
                        worker.active_ordinal is not None
                        or worker.waiting_for_run_fixture
                    ):
                        continue
                    ordinal = _next_runnable_ordinal(
                        pending, canonical_manifest, active_mutexes
                    )
                    if ordinal is None:
                        continue
                    worker.connection.send(_Execute(ordinal))
                    worker.active_ordinal = ordinal
                    if (mutex := canonical_manifest[ordinal].mutex) is not None:
                        active_mutexes.add(mutex)
                    receive_task = asyncio.create_task(
                        asyncio.to_thread(worker.connection.recv)
                    )
                    receive_tasks[receive_task] = worker

            if not receive_tasks and pending:
                if replacements_needed:
                    continue
                msg = "parallel scheduler has pending tests but no active workers"
                raise RunInfrastructureError(msg)
            if not receive_tasks:
                break

            completed, _ = await asyncio.wait(
                receive_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for receive_task in completed:
                worker = receive_tasks.pop(receive_task)
                ordinal = cast("int", worker.active_ordinal)
                try:
                    message = receive_task.result()
                except EOFError, OSError:
                    worker.active_ordinal = None
                    if (mutex := canonical_manifest[ordinal].mutex) is not None:
                        active_mutexes.remove(mutex)
                    await asyncio.to_thread(worker.process.join)
                    results_by_ordinal[ordinal] = _worker_crash_result(
                        canonical_manifest[ordinal],
                        exit_code=worker.process.exitcode,
                    )
                    worker_processes.remove(worker)
                    worker.connection.close()
                    replacements_needed += 1
                    continue
                if publication.request(message):
                    worker.waiting_for_run_fixture = True
                    continue
                if not isinstance(message, _ExecutionFinished):
                    msg = f"worker {worker.identifier + 1} sent invalid result message"
                    raise RunInfrastructureError(msg)
                worker.active_ordinal = None
                if (mutex := canonical_manifest[ordinal].mutex) is not None:
                    active_mutexes.remove(mutex)
                results_by_ordinal[ordinal] = message.result

            while next_report_ordinal in results_by_ordinal:
                test_result = results_by_ordinal.pop(next_report_ordinal)
                reporter.test_finished(test_result)
                reported_results.append(result_for_retention(reporter, test_result))
                next_report_ordinal += 1
                if fail_fast and test_result.is_actionable_failure:
                    pending.clear()

        retained_directories: list[RetainedDirectory] = []
        session_failures: list[TeardownFailure] = []
        session_outputs: list[str] = []
        session_warnings: list[str] = []
        for worker in worker_processes:
            worker.connection.send(_Shutdown())
        for worker in worker_processes:
            message = await asyncio.to_thread(worker.connection.recv)
            if not isinstance(message, _WorkerStopped):
                msg = f"worker {worker.identifier + 1} failed during shutdown"
                raise RunInfrastructureError(msg)
            retained_directories.extend(message.retained_directories)
            session_failures.extend(message.session_teardown_failures)
            if message.session_teardown_output:
                session_outputs.append(message.session_teardown_output)
            session_warnings.extend(message.session_teardown_warnings)
            worker.connection.close()
            await asyncio.to_thread(worker.process.join)

        host_connection.send(_Shutdown())
        host_message = await asyncio.to_thread(host_connection.recv)
        if not isinstance(host_message, _HostStopped):
            msg = "fixture host failed during run fixture teardown"
            raise RunInfrastructureError(msg)
        run_failures = list(host_message.run_teardown_failures)
        host_connection.close()
        await asyncio.to_thread(host_process.join)
        run_output = host_message.run_teardown_output
        run_warnings = host_message.run_teardown_warnings
        session_output = "".join(session_outputs) or None
        if teardown_diagnostics is not None:
            teardown_diagnostics.run_output = run_output
            teardown_diagnostics.run_warnings = run_warnings
            teardown_diagnostics.session_output = session_output
            teardown_diagnostics.session_warnings = tuple(session_warnings)
        publication.raise_if_interrupted()
        completed_run = RunResult.from_execution(
            retained_directories=tuple(retained_directories),
            collection_output=canonical_bootstrap.collection_output,
            collection_warnings=canonical_bootstrap.collection_warnings,
            run_teardown_failures=run_failures,
            run_teardown_output=(publication.output + (run_output or "") or None),
            run_teardown_warnings=run_warnings,
            session_teardown_failures=session_failures,
            session_teardown_output=session_output,
            session_teardown_warnings=tuple(session_warnings),
            test_results=reported_results,
            selected_tests=len(canonical_manifest),
            total_duration=time.monotonic() - started_at,
        )
        reporter.run_finished(completed_run)
        return completed_run
    finally:
        for worker in worker_processes:
            if worker.process.is_alive():
                await _stop_process(worker.process, worker.connection)
        if host_process.is_alive():
            await _stop_process(host_process, host_connection)
