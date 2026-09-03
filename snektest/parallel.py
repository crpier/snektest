"""Spawn-based persistent process execution and mutex-aware scheduling."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from multiprocessing import get_context
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from pickle import loads
from typing import Any, Literal, Protocol, cast

from snektest.annotations import AsyncFixture, Coroutine, Fixture
from snektest.benchmark_baseline import BenchmarkBaseline
from snektest.collection import collect_tests_from_filters
from snektest.decorators import RunFixtureIdentity, get_run_fixture_catalog
from snektest.execution import execute_test, teardown_session_fixtures
from snektest.fixtures import (
    FixtureRegistry,
    use_registry,
    use_run_fixture_loader,
)
from snektest.models import (
    CollectionError,
    EmptyCollectionError,
    ErrorResult,
    ExceptionDiagnostic,
    FilterItem,
    FixtureError,
    InvalidTestDefinitionError,
    RunInfrastructureError,
    RunTeardownDiagnostics,
    TeardownFailure,
    TestCase,
    TestName,
    TestResult,
)
from snektest.output import maybe_capture_output
from snektest.reporting import RunReporter, result_for_retention


class _ProcessConnection(Protocol):
    """Operations shared by Unix `Connection` and Windows `PipeConnection`."""

    def close(self) -> None: ...

    def recv(self) -> object: ...

    def send(self, obj: Any) -> None: ...


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


@dataclass(frozen=True)
class _BootstrapFailed:
    exception_type: str
    message: str


@dataclass(frozen=True)
class _Execute:
    ordinal: int


@dataclass(frozen=True)
class _ExecutionFinished:
    result: TestResult


@dataclass(frozen=True)
class _RunFixtureRequested:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _LoadRunFixture:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _RunFixtureLoaded:
    identity: RunFixtureIdentity
    output: str
    payload: bytes


@dataclass(frozen=True)
class _RunFixtureFailed:
    identity: RunFixtureIdentity
    message: str
    output: str = ""


@dataclass(frozen=True)
class _StageRunFixture:
    identity: RunFixtureIdentity
    payload: bytes


@dataclass(frozen=True)
class _RunFixtureStageAck:
    identity: RunFixtureIdentity
    message: str | None = None


@dataclass(frozen=True)
class _CommitRunFixture:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _DiscardRunFixture:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _RunFixtureUnavailable:
    identity: RunFixtureIdentity
    message: str


@dataclass(frozen=True)
class _Shutdown: ...


@dataclass(frozen=True)
class _WorkerStopped:
    session_teardown_failures: tuple[TeardownFailure, ...]
    session_teardown_output: str | None
    session_teardown_warnings: tuple[str, ...]


@dataclass(frozen=True)
class _HostStopped:
    run_teardown_failures: tuple[TeardownFailure, ...]
    run_teardown_output: str | None
    run_teardown_warnings: tuple[str, ...]


type _ParentToWorker = (
    _CommitRunFixture
    | _DiscardRunFixture
    | _Execute
    | _RunFixtureUnavailable
    | _Shutdown
    | _StageRunFixture
)


@dataclass
class _Worker:
    """Coordinator-owned connection and lifecycle state for one process."""

    connection: _ProcessConnection
    identifier: int
    process: BaseProcess
    active_ordinal: int | None = None
    waiting_for_run_fixture: RunFixtureIdentity | None = None


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
    raw_filters: tuple[str, ...], mark: str | None, *, allow_empty: bool = False
) -> list[TestCase]:
    return collect_tests_from_filters(
        [FilterItem(raw_filter) for raw_filter in raw_filters],
        allow_empty=allow_empty,
        mark=mark,
    )


async def _await_run_fixture_payload(
    payload: Coroutine[bytes],
    timeout: float | None,  # noqa: ASYNC109
) -> bytes:
    if timeout is None:
        return await payload
    # Reserve part of the transaction budget for serialization and fleet staging.
    async with asyncio.timeout(timeout / 2):
        return await payload


async def _load_async_run_fixture_payload(
    registry: FixtureRegistry,
    handle: AsyncFixture[Any],
    timeout: float | None,  # noqa: ASYNC109
) -> bytes:
    payload = cast("Coroutine[bytes]", registry.load_run_payload(handle))
    return await _await_run_fixture_payload(payload, timeout)


def _host_main(  # noqa: PLR0913
    connection: _ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    allow_empty: bool,  # noqa: FBT001
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
) -> None:
    """Collect the canonical manifest and remain alive as the fixture-host process."""
    try:
        with maybe_capture_output(capture_output):
            test_cases = _collect_child_plan(raw_filters, mark, allow_empty=allow_empty)
        catalog = get_run_fixture_catalog()
        connection.send(
            _BootstrapReady(
                manifest=_manifest(test_cases),
                run_fixture_catalog=tuple(sorted(catalog)),
            )
        )
    except BaseException as exc:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send(_BootstrapFailed(type(exc).__name__, str(exc)))
        connection.close()
        return

    registry = FixtureRegistry()
    with use_registry(registry), asyncio.Runner() as runner:
        while True:
            message = connection.recv()
            if isinstance(message, _Shutdown):
                with maybe_capture_output(capture_output) as (output, warnings):
                    failures = runner.run(
                        registry.teardown_run_fixtures(cleanup_timeout=timeout)
                    )
                connection.send(
                    _HostStopped(
                        tuple(failures),
                        output.getvalue() or None,
                        tuple(warnings),
                    )
                )
                break
            if not isinstance(message, _LoadRunFixture):
                msg = (
                    f"Fixture host received unexpected message {type(message).__name__}"
                )
                connection.send(_RunFixtureFailed(("", ""), msg))
                continue
            output = None
            try:
                with maybe_capture_output(capture_output) as (output, _):
                    handle = catalog[message.identity]()
                    if isinstance(handle, AsyncFixture):
                        payload = runner.run(
                            _load_async_run_fixture_payload(
                                registry,
                                handle,
                                timeout,
                            )
                        )
                    else:
                        payload = cast("bytes", registry.load_run_payload(handle))
            except BaseException as exc:
                connection.send(
                    _RunFixtureFailed(
                        message.identity,
                        (
                            f"Run fixture {message.identity[0]}."
                            f"{message.identity[1]} publication failed: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        output.getvalue() if output is not None else "",
                    )
                )
            else:
                connection.send(
                    _RunFixtureLoaded(
                        message.identity,
                        output.getvalue(),
                        payload,
                    )
                )
    connection.close()


class _RemoteRunFixtureLoader:
    """Worker-side descriptor cache driven by coordinator publication messages."""

    def __init__(self, connection: _ProcessConnection) -> None:
        self._committed: dict[RunFixtureIdentity, object] = {}
        self._connection: _ProcessConnection = connection
        self._failures: dict[RunFixtureIdentity, str] = {}
        self._staged: dict[RunFixtureIdentity, object] = {}

    def __call__[R](self, handle: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
        identity = (
            cast("str", getattr(handle.key, "__module__", "")),
            cast("str", getattr(handle.key, "__qualname__", "")),
        )
        if isinstance(handle, AsyncFixture):

            async def load_async() -> R:
                return cast("R", await asyncio.to_thread(self._load, identity))

            return cast("Coroutine[R]", load_async())
        return cast("R", self._load(identity))

    def process_control(self, message: _ParentToWorker) -> bool:
        """Apply one publication message, returning whether it was recognized."""
        if isinstance(message, _StageRunFixture):
            try:
                self._staged[message.identity] = loads(message.payload)  # noqa: S301
            except BaseException as exc:
                self._connection.send(
                    _RunFixtureStageAck(
                        message.identity,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                self._connection.send(_RunFixtureStageAck(message.identity))
            return True
        if isinstance(message, _CommitRunFixture):
            self._committed[message.identity] = self._staged.pop(message.identity)
            self._failures.pop(message.identity, None)
            return True
        if isinstance(message, _DiscardRunFixture):
            self._staged.pop(message.identity, None)
            return True
        if isinstance(message, _RunFixtureUnavailable):
            self._staged.pop(message.identity, None)
            self._failures[message.identity] = message.message
            return True
        return False

    def _load(self, identity: RunFixtureIdentity) -> object:
        if identity in self._failures:
            raise FixtureError(self._failures[identity])
        if identity in self._committed:
            return self._committed[identity]

        self._connection.send(_RunFixtureRequested(identity))
        while True:
            message = cast("_ParentToWorker", self._connection.recv())
            if not self.process_control(message):
                msg = f"Expected run fixture publication, got {type(message).__name__}"
                raise FixtureError(msg)
            if isinstance(message, _CommitRunFixture) and message.identity == identity:
                return self._committed[identity]
            if (
                isinstance(message, _RunFixtureUnavailable)
                and message.identity == identity
            ):
                raise FixtureError(message.message)


def _worker_main(  # noqa: PLR0913
    connection: _ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
    benchmark_baseline: BenchmarkBaseline | None,
) -> None:
    """Run a worker with interactive stdin disabled."""
    with Path(os.devnull).open() as worker_stdin:
        sys.stdin = worker_stdin
        _run_worker(
            connection,
            raw_filters,
            mark,
            capture_output,
            timeout,
            benchmark_baseline,
        )


def _run_worker(  # noqa: PLR0913
    connection: _ProcessConnection,
    raw_filters: tuple[str, ...],
    mark: str | None,
    capture_output: bool,  # noqa: FBT001
    timeout: float | None,
    benchmark_baseline: BenchmarkBaseline | None,
) -> None:
    """Import one local plan and execute assigned ordinals on one event loop."""
    try:
        with maybe_capture_output(capture_output):
            test_cases = _collect_child_plan(raw_filters, mark)
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
    run_fixture_loader = _RemoteRunFixtureLoader(connection)
    with (
        use_registry(registry),
        use_run_fixture_loader(run_fixture_loader),
        asyncio.Runner() as runner,
    ):
        while True:
            message = cast("_ParentToWorker", connection.recv())
            if run_fixture_loader.process_control(message):
                continue
            if isinstance(message, _Shutdown):
                failures, output, warnings = runner.run(
                    teardown_session_fixtures(
                        capture_output=capture_output, cleanup_timeout=timeout
                    )
                )
                connection.send(
                    _WorkerStopped(
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
                execute_test(
                    test_case,
                    capture_output=capture_output,
                    timeout=timeout,
                    benchmark_baseline=benchmark_baseline,
                )
            )
            connection.send(_ExecutionFinished(result))
    connection.close()


def _spawn_process(
    context: SpawnContext,
    target: Callable[..., object],
    args: tuple[object, ...],
    *,
    name: str,
) -> tuple[BaseProcess, _ProcessConnection]:
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=target,
        args=(child_connection, *args),
        daemon=False,
        name=name,
    )
    process.start()
    child_connection.close()
    return process, parent_connection


async def _receive_bootstrap(
    connection: _ProcessConnection,
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
        if collection_owner and message.exception_type == "EmptyCollectionError":
            raise EmptyCollectionError(message.message)
        if collection_owner and message.exception_type == "InvalidTestDefinitionError":
            raise InvalidTestDefinitionError(message.message)
        if collection_owner and message.exception_type == "CollectionError":
            raise CollectionError(message.message)
        msg = (
            f"{child_name} bootstrap failed: "
            f"{message.exception_type}: {message.message}"
        )
        raise RunInfrastructureError(msg)
    if not isinstance(message, _BootstrapReady):
        msg = f"{child_name} sent invalid bootstrap message"
        raise RunInfrastructureError(msg)
    return message


async def _stop_process(process: BaseProcess, connection: _ProcessConnection) -> None:
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
    publication_failures: dict[RunFixtureIdentity, str],
    published_descriptors: dict[RunFixtureIdentity, bytes],
    raw_filters: tuple[str, ...],
    timeout: float | None,  # noqa: ASYNC109
) -> _Worker:
    """Start one worker serially and require the canonical structural manifest."""
    process, connection = _spawn_process(
        context,
        _worker_main,
        (raw_filters, mark, capture_output, timeout, benchmark_baseline),
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
    if worker_bootstrap != canonical_bootstrap:
        await _stop_process(process, connection)
        msg = f"worker {identifier + 1} collected a different test manifest"
        raise RunInfrastructureError(msg)
    for identity, payload in published_descriptors.items():
        connection.send(_StageRunFixture(identity, payload))
        acknowledgement = await asyncio.to_thread(connection.recv)
        if not isinstance(acknowledgement, _RunFixtureStageAck):
            await _stop_process(process, connection)
            msg = f"worker {identifier + 1} failed to restore run fixture {identity}"
            raise RunInfrastructureError(msg)
        if acknowledgement.message is not None:
            await _stop_process(process, connection)
            msg = (
                f"worker {identifier + 1} could not decode run fixture {identity}: "
                f"{acknowledgement.message}"
            )
            raise RunInfrastructureError(msg)
        connection.send(_CommitRunFixture(identity))
    for identity, message in publication_failures.items():
        connection.send(_RunFixtureUnavailable(identity, message))
    return _Worker(
        connection=connection,
        identifier=identifier,
        process=process,
    )


async def _publish_run_fixture(  # noqa: C901, PLR0913
    identity: RunFixtureIdentity,
    *,
    host_connection: _ProcessConnection,
    publication_failures: dict[RunFixtureIdentity, str],
    published_descriptors: dict[RunFixtureIdentity, bytes],
    lifecycle_outputs: list[str],
    workers: list[_Worker],
) -> _CommitRunFixture | _RunFixtureUnavailable:
    """Stage one host descriptor everywhere before making any copy visible."""
    if identity in published_descriptors or identity in publication_failures:
        if identity in published_descriptors:
            return _CommitRunFixture(identity)
        return _RunFixtureUnavailable(identity, publication_failures[identity])
    host_connection.send(_LoadRunFixture(identity))
    try:
        host_message = await asyncio.to_thread(host_connection.recv)
    except (EOFError, OSError) as exc:
        msg = "fixture host exited during run fixture setup"
        raise RunInfrastructureError(msg) from exc
    if isinstance(host_message, _RunFixtureFailed):
        if host_message.output:
            lifecycle_outputs.append(host_message.output)
        publication_failures[identity] = host_message.message
        return _RunFixtureUnavailable(identity, host_message.message)
    if not isinstance(host_message, _RunFixtureLoaded):
        msg = "fixture host sent an invalid run fixture response"
        raise RunInfrastructureError(msg)
    if host_message.output:
        lifecycle_outputs.append(host_message.output)

    for worker in workers:
        worker.connection.send(_StageRunFixture(identity, host_message.payload))
    acknowledgements = await asyncio.gather(
        *(asyncio.to_thread(worker.connection.recv) for worker in workers)
    )
    decode_errors = [
        acknowledgement.message
        for acknowledgement in acknowledgements
        if isinstance(acknowledgement, _RunFixtureStageAck)
        and acknowledgement.message is not None
    ]
    acknowledgements_valid = all(
        isinstance(acknowledgement, _RunFixtureStageAck)
        and acknowledgement.identity == identity
        for acknowledgement in acknowledgements
    )
    if not acknowledgements_valid:
        msg = f"Invalid staging acknowledgement for run fixture {identity}"
        raise RunInfrastructureError(msg)
    if decode_errors:
        message = f"Run fixture {identity} publication failed: {decode_errors[0]}"
        publication_failures[identity] = message
        for worker in workers:
            worker.connection.send(_DiscardRunFixture(identity))
        return _RunFixtureUnavailable(identity, message)

    published_descriptors[identity] = host_message.payload
    return _CommitRunFixture(identity)


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
    mark: str | None,
    reporter: RunReporter,
    timeout: float | None,  # noqa: ASYNC109
    workers: int | Literal["auto"],
    teardown_diagnostics: RunTeardownDiagnostics | None = None,
) -> tuple[list[TestResult], list[TeardownFailure], list[TeardownFailure]]:
    """Run a canonical plan across persistent spawn workers."""
    started_at = time.monotonic()
    context = get_context("spawn")
    raw_filters = tuple(str(filter_item) for filter_item in filter_items)
    host_process, host_connection = _spawn_process(
        context,
        _host_main,
        (raw_filters, mark, allow_empty, capture_output, timeout),
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
        publication_failures: dict[RunFixtureIdentity, str] = {}
        published_descriptors: dict[RunFixtureIdentity, bytes] = {}
        run_lifecycle_outputs: list[str] = []
        for identifier in range(requested_workers):
            worker_processes.append(  # noqa: PERF401
                await _start_worker(
                    context,
                    canonical_bootstrap=canonical_bootstrap,
                    capture_output=capture_output,
                    benchmark_baseline=benchmark_baseline,
                    identifier=identifier,
                    mark=mark,
                    publication_failures=publication_failures,
                    published_descriptors=published_descriptors,
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
        run_ahead_limit = requested_workers * 2
        run_fixture_requests: list[RunFixtureIdentity] = []

        while pending or receive_tasks or run_fixture_requests:
            if run_fixture_requests and not receive_tasks:
                publication_releases: list[
                    _CommitRunFixture | _RunFixtureUnavailable
                ] = []
                while run_fixture_requests:
                    identity = run_fixture_requests.pop(0)
                    publication = _publish_run_fixture(
                        identity,
                        host_connection=host_connection,
                        publication_failures=publication_failures,
                        published_descriptors=published_descriptors,
                        lifecycle_outputs=run_lifecycle_outputs,
                        workers=worker_processes,
                    )
                    try:
                        if timeout is None:
                            release = await publication
                        else:
                            release = await asyncio.wait_for(
                                publication,
                                timeout=timeout,
                            )
                    except TimeoutError:
                        msg = (
                            f"Run fixture {identity} publication did not finish "
                            f"within {timeout:g}s"
                        )
                        raise RunInfrastructureError(msg) from None
                    publication_releases.append(release)
                for release in publication_releases:
                    for worker in worker_processes:
                        worker.connection.send(release)
                for worker in worker_processes:
                    if worker.waiting_for_run_fixture is None:
                        continue
                    worker.waiting_for_run_fixture = None
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
                            identifier=next_worker_identifier,
                            mark=mark,
                            publication_failures=publication_failures,
                            published_descriptors=published_descriptors,
                            raw_filters=raw_filters,
                            timeout=timeout,
                        )
                    )
                    next_worker_identifier += 1
                replacements_needed = 0

            if not replacements_needed and not run_fixture_requests:
                for worker in worker_processes:
                    unreported_case_count = len(results_by_ordinal) + sum(
                        active_worker.active_ordinal is not None
                        for active_worker in worker_processes
                    )
                    if unreported_case_count >= run_ahead_limit:
                        break
                    if (
                        worker.active_ordinal is not None
                        or worker.waiting_for_run_fixture is not None
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
                except (EOFError, OSError):
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
                if isinstance(message, _RunFixtureRequested):
                    worker.waiting_for_run_fixture = message.identity
                    if message.identity not in run_fixture_requests:
                        run_fixture_requests.append(message.identity)
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
        if reported_results and (session_warnings or run_warnings):
            final_result = reported_results[-1]
            reported_results[-1] = replace(
                final_result,
                warnings=(
                    *final_result.warnings,
                    *session_warnings,
                    *run_warnings,
                ),
            )
        reporter.run_finished(
            run_teardown_failures=run_failures,
            run_teardown_output=(
                "".join(
                    [
                        *run_lifecycle_outputs,
                        run_output or "",
                    ]
                )
                or None
            ),
            test_results=reported_results,
            session_teardown_failures=session_failures,
            session_teardown_output=session_output,
            total_duration=time.monotonic() - started_at,
        )
        return reported_results, session_failures, run_failures
    finally:
        for worker in worker_processes:
            if worker.process.is_alive():
                await _stop_process(worker.process, worker.connection)
        if host_process.is_alive():
            await _stop_process(host_process, host_connection)
