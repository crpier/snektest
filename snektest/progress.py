"""Opt-in, bounded lifecycle sidecars, independent of capture and reporters.

Each process appends under a native advisory lock. No scheduler/publication
connection carries observation traffic, and no queue or live frame is retained.
"""

lazy import asyncio
lazy import json
lazy import os
lazy import sys
lazy import time
lazy from collections.abc import Generator
lazy from contextlib import contextmanager, suppress
lazy from contextvars import ContextVar
lazy from dataclasses import dataclass
lazy from pathlib import Path
lazy from threading import TIMEOUT_MAX, Event, Lock, Thread
lazy from typing import IO
lazy from uuid import uuid4

lazy from snektest.models import BadRequestError

if sys.platform == "win32":
    lazy import msvcrt
else:
    lazy import fcntl

_MAX_BYTES = 16 * 1024 * 1024
_MAX_ACTIVE = 128
_TEXT_LIMIT = 1024


@dataclass(frozen=True)
class ProgressSettings:
    path: str
    run_id: str
    started: float
    interval: float


_current: ContextVar[ProgressWriter | None] = ContextVar(
    "snektest_progress", default=None
)
_case: ContextVar[tuple[str, int] | None] = ContextVar(
    "snektest_progress_case", default=None
)


@contextmanager
def _file_lock(stream: IO[str]) -> Generator[None]:
    # OS releases these locks on process death, unlike lock-file ownership.
    if sys.platform == "win32":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class ProgressWriter:
    """One process's bounded metadata and serialized, flushed JSONL appends."""

    def __init__(self, settings: ProgressSettings, worker: str) -> None:
        self.settings = settings
        self.worker = worker
        self._stream = Path(settings.path).open("r+", encoding="utf-8")  # noqa: SIM115 -- owned by observation lifetime
        self._lock_stream = Path(settings.path + ".lock").open("r+", encoding="utf-8")  # noqa: SIM115 -- separate lock keeps Windows readers unblocked
        self._lock = Lock()
        self._stop = Event()
        self._active: dict[str, dict[str, object]] = {}
        self._omitted = 0
        self._sequence = 0
        self._truncated = False
        self._failed = False
        self._closed = False
        self.interrupted = False
        self._completion: dict[str, object] | None = None
        self._thread = Thread(
            target=self._heartbeat, name="snektest-progress", daemon=True
        )

    def _write(self, event: str, fields: dict[str, object]) -> None:
        if self._failed or self._closed:
            return
        self._sequence += 1
        document = {
            "schema_version": 1,
            "run_id": self.settings.run_id,
            "event": event,
            "sequence": self._sequence,
            "elapsed": max(0.0, time.monotonic() - self.settings.started),
            "pid": os.getpid(),
            "worker": self.worker,
            **fields,
        }
        line = json.dumps(document, ensure_ascii=True) + "\n"
        if self._truncated and event != "run_finished":
            return
        try:
            with _file_lock(self._lock_stream):
                size = os.fstat(self._stream.fileno()).st_size
                # A previous large heartbeat may hit the cap before a small
                # record would do so. Inspect only a bounded suffix.
                self._stream.seek(max(0, size - 512))
                truncated = '"event": "stream_truncated"' in self._stream.read()
                self._stream.seek(0, os.SEEK_END)
                if event == "run_finished":
                    document["truncated"] = self._truncated or truncated
                    line = json.dumps(document, ensure_ascii=True) + "\n"
                elif truncated or size + len(line) >= _MAX_BYTES:
                    if not truncated:
                        _ = self._stream.write(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "run_id": self.settings.run_id,
                                    "event": "stream_truncated",
                                    "limit_bytes": _MAX_BYTES,
                                }
                            )
                            + "\n"
                        )
                        self._stream.flush()
                    self._truncated = True
                    return
                _ = self._stream.write(line)
                self._stream.flush()
        except OSError:
            self._failed = True

    def emit(self, event: str, **fields: object) -> None:
        with self._lock:
            self._write(event, fields)

    def start(self, phase: str, fields: dict[str, object]) -> str:
        span = uuid4().hex
        data = {"span": span, "phase": phase, **fields}
        with self._lock:
            if self._closed:
                return span
            if len(self._active) < _MAX_ACTIVE:
                self._active[span] = {
                    **data,
                    "started_elapsed": max(
                        0.0, time.monotonic() - self.settings.started
                    ),
                }
            else:
                self._omitted += 1
            self._write(f"{phase}_started", data)
        return span

    def finish(
        self, span: str, phase: str, status: str, fields: dict[str, object]
    ) -> None:
        with self._lock:
            if self._closed:
                return
            if self._active.pop(span, None) is None:
                self._omitted -= 1
            self._write(
                f"{phase}_finished",
                {"span": span, "phase": phase, "status": status, **fields},
            )

    def _heartbeat(self) -> None:
        while not self._stop.wait(min(self.settings.interval, TIMEOUT_MAX)):
            with self._lock:
                self._write(
                    "still_running",
                    {
                        "active": list(self._active.values())[:32],
                        "active_omitted": self._omitted
                        + max(0, len(self._active) - 32),
                    },
                )

    def start_observing(self) -> None:
        if self.worker == "coordinator":
            self.emit("run_started")
        self.emit("process_started")
        self._thread.start()

    def complete(self, status: str, exit_code: int) -> None:
        self._completion = {
            "status": "interrupted" if self.interrupted else status,
            "exit_code": exit_code,
        }

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._write(
                "process_finished", {"active_spans": len(self._active) + self._omitted}
            )
            if self._completion is not None:
                self._write("run_finished", self._completion)
            self._closed = True
            for stream in (self._stream, self._lock_stream):
                try:
                    stream.close()
                except OSError:
                    # A failed buffered append can fail again during close.
                    # Keep only a flag, never an exception with live frames, and
                    # never emit a second JSON error after the final result.
                    self._failed = True
        if self.worker == "coordinator":
            with suppress(OSError):
                Path(self.settings.path + ".lock").unlink(missing_ok=True)
        if self._failed:
            print(
                "Lifecycle progress output failed; the sidecar is incomplete",
                file=sys.stderr,
            )


def note_interruption() -> None:
    writer = _current.get()
    if writer is not None:
        writer.interrupted = True


def progress_settings() -> ProgressSettings | None:
    writer = _current.get()
    return writer.settings if writer is not None else None


@contextmanager
def observe_process(
    settings: ProgressSettings | None, worker: str
) -> Generator[ProgressWriter | None]:
    if settings is None:
        yield None
        return
    writer = ProgressWriter(settings, worker)
    token = _current.set(writer)
    try:
        writer.start_observing()
        yield writer
    finally:
        _current.reset(token)
        writer.close()


@contextmanager
def observe_run(
    path: Path | None, *, interval: float = 5.0
) -> Generator[ProgressWriter | None]:
    if path is None:
        yield None
        return
    if _current.get() is not None:
        msg = "Lifecycle progress observation cannot be nested"
        raise BadRequestError(msg)
    try:
        # Exclusive creation protects source files and existing evidence, including
        # symlinks; consumers should read a fresh path for each command.
        with path.open("x", encoding="utf-8"):
            pass
        try:
            with Path(str(path) + ".lock").open("x", encoding="utf-8"):
                pass
        except OSError:
            path.unlink()
            raise
    except OSError as error:
        msg = f"Could not create lifecycle progress output `{path}`"
        raise BadRequestError(msg) from error
    settings = ProgressSettings(
        str(path.resolve()), uuid4().hex, time.monotonic(), interval
    )
    case_token = _case.set(None)
    try:
        with observe_process(settings, "coordinator") as writer:
            yield writer
    finally:
        _case.reset(case_token)


@contextmanager
def case_context(selector: str, ordinal: int) -> Generator[None]:
    if _case.get() is not None:
        # A test may itself exercise a programmatic runner or execute_test.
        # Its inner ordinals are not occurrences in this command's manifest.
        writer_token = _current.set(None)
        try:
            yield
        finally:
            _current.reset(writer_token)
        return
    token = _case.set((selector, ordinal))
    try:
        yield
    finally:
        _case.reset(token)


@dataclass
class Activity:
    status: str = "completed"


@contextmanager
def activity(
    phase: str,
    *,
    file: str | None = None,
    fixture: str | None = None,
    scope: str | None = None,
) -> Generator[Activity]:
    outcome = Activity()
    writer = _current.get()
    if writer is None:
        yield outcome
        return
    case = _case.get()
    fields: dict[str, object] = {
        "selector": case[0][:_TEXT_LIMIT] if case else None,
        "selector_truncated": bool(case and len(case[0]) > _TEXT_LIMIT),
        "ordinal": case[1] if case else None,
        "attempt": 1 if case else None,
        "file": file[:_TEXT_LIMIT] if file is not None else None,
        "fixture": fixture[:_TEXT_LIMIT] if fixture is not None else None,
        "scope": scope,
        "metadata_truncated": any(
            value is not None and len(value) > _TEXT_LIMIT for value in (file, fixture)
        ),
    }
    span = writer.start(phase, fields)
    try:
        yield outcome
    except BaseException as error:
        outcome.status = (
            "interrupted"
            if isinstance(
                error,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit),
            )
            else "error"
        )
        raise
    finally:
        writer.finish(span, phase, outcome.status, fields)
