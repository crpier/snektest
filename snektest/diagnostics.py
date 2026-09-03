"""Snapshot live exceptions into immutable process-neutral diagnostics."""

from __future__ import annotations

import linecache
import traceback as traceback_module
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from snektest.models import (
    AssertionDiagnostic,
    AssertionFailure,
    DiagnosticDict,
    DiagnosticFrame,
    DiagnosticList,
    DiagnosticRepr,
    DiagnosticValue,
    ExceptionDiagnostic,
)

_MAX_ASSERTION_COLLECTION_ITEMS = 100
"""Most collection items retained for one assertion operand."""

_MAX_ASSERTION_DEPTH = 8
"""Deepest nested assertion value retained structurally."""

_MAX_DIAGNOSTIC_TEXT = 100_000
"""Most characters retained from one user-controlled string or representation."""

_MAX_EXCEPTION_DEPTH = 20
"""Deepest exception chain or group retained."""

_MAX_EXCEPTION_GROUP_MEMBERS = 100
"""Most direct members retained from one exception group."""

_MAX_EXCEPTION_NODES = 1_000
"""Most unique exceptions retained across one complete diagnostic graph."""

_MAX_TRACEBACK_FRAMES = 200
"""Most user-code frames retained from one traceback."""

_PACKAGE_PATH = Path(__file__).resolve().parent


def _bounded_text(text: str) -> str:
    if len(text) <= _MAX_DIAGNOSTIC_TEXT:
        return text
    return f"{text[: _MAX_DIAGNOSTIC_TEXT - 3]}..."


def _safe_string(value: object) -> str:
    try:
        return _bounded_text(str(value))
    except BaseException as exc:
        return f"<str failed: {type(exc).__name__}>"


def _safe_repr(value: object) -> str:
    try:
        return _bounded_text(repr(value))
    except BaseException as exc:
        return f"<repr failed: {type(exc).__name__}>"


def _snapshot_value(  # noqa: C901, PLR0911
    value: object,
    *,
    depth: int = 0,
    seen: frozenset[int] = frozenset(),
) -> DiagnosticValue:
    """Retain safe collection structure without retaining arbitrary objects."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_DIAGNOSTIC_TEXT:
            return value
        return DiagnosticRepr(_safe_repr(value))
    if isinstance(value, bytes):
        if len(value) <= _MAX_DIAGNOSTIC_TEXT:
            return value
        return DiagnosticRepr(_safe_repr(value))
    if depth >= _MAX_ASSERTION_DEPTH or id(value) in seen:
        return DiagnosticRepr(_safe_repr(value))

    nested_seen = seen | {id(value)}
    if isinstance(value, list):
        if len(value) > _MAX_ASSERTION_COLLECTION_ITEMS:
            return DiagnosticRepr(_safe_repr(value))
        return DiagnosticList(
            tuple(
                _snapshot_value(item, depth=depth + 1, seen=nested_seen)
                for item in value
            )
        )
    if isinstance(value, dict):
        if len(value) > _MAX_ASSERTION_COLLECTION_ITEMS:
            return DiagnosticRepr(_safe_repr(value))
        return DiagnosticDict(
            tuple(
                (
                    _snapshot_value(key, depth=depth + 1, seen=nested_seen),
                    _snapshot_value(item, depth=depth + 1, seen=nested_seen),
                )
                for key, item in value.items()
            )
        )
    return DiagnosticRepr(_safe_repr(value))


def snapshot_assertion_failure(exception: AssertionFailure) -> AssertionDiagnostic:
    """Capture only the operands needed by the selected assertion renderer."""
    actual = exception.actual
    expected = exception.expected
    message = _safe_string(exception)
    if (
        isinstance(actual, list)
        and isinstance(expected, list)
        and len(actual) <= _MAX_ASSERTION_COLLECTION_ITEMS
        and len(expected) <= _MAX_ASSERTION_COLLECTION_ITEMS
    ):
        return AssertionDiagnostic(
            actual=_snapshot_value(actual),
            expected=_snapshot_value(expected),
            kind="list",
            message=message,
        )
    if (
        isinstance(actual, dict)
        and isinstance(expected, dict)
        and len(actual) <= _MAX_ASSERTION_COLLECTION_ITEMS
        and len(expected) <= _MAX_ASSERTION_COLLECTION_ITEMS
    ):
        return AssertionDiagnostic(
            actual=_snapshot_value(actual),
            expected=_snapshot_value(expected),
            kind="dict",
            message=message,
        )
    if (
        isinstance(actual, str)
        and isinstance(expected, str)
        and ("\n" in actual or "\n" in expected)
    ):
        return AssertionDiagnostic(
            actual=_bounded_text(actual),
            expected=_bounded_text(expected),
            kind="multiline_string",
            message=message,
        )
    return AssertionDiagnostic(
        actual=None,
        expected=None,
        kind="plain",
        message=message,
    )


def _is_internal_frame(filename: str) -> bool:
    try:
        return Path(filename).resolve().is_relative_to(_PACKAGE_PATH)
    except OSError:
        return False


def _snapshot_frames(traceback: TracebackType | None) -> tuple[DiagnosticFrame, ...]:
    frames: list[DiagnosticFrame] = []
    current = traceback
    while current is not None and len(frames) < _MAX_TRACEBACK_FRAMES:
        filename = current.tb_frame.f_code.co_filename
        if not _is_internal_frame(filename):
            source_line = linecache.getline(filename, current.tb_lineno).rstrip()
            frames.append(
                DiagnosticFrame(
                    filename=filename,
                    function_name=current.tb_frame.f_code.co_name,
                    lineno=current.tb_lineno,
                    source_line=source_line or None,
                )
            )
        current = current.tb_next
    return tuple(frames)


def _qualified_type_name(exception_type: type[BaseException]) -> str:
    if exception_type.__module__ == "builtins":
        return exception_type.__qualname__
    return f"{exception_type.__module__}.{exception_type.__qualname__}"


def _snapshot_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
    *,
    depth: int,
    seen: set[int],
) -> ExceptionDiagnostic:
    if (
        depth >= _MAX_EXCEPTION_DEPTH
        or id(exception) in seen
        or len(seen) >= _MAX_EXCEPTION_NODES
    ):
        return ExceptionDiagnostic(
            frames=(),
            message=_safe_string(exception),
            qualified_type_name=_qualified_type_name(exception_type),
            type_name=exception_type.__name__,
        )

    seen.add(id(exception))
    cause = exception.__cause__
    context = exception.__context__
    grouped = exception.exceptions if isinstance(exception, BaseExceptionGroup) else ()
    return ExceptionDiagnostic(
        frames=_snapshot_frames(traceback),
        message=_safe_string(exception),
        qualified_type_name=_qualified_type_name(exception_type),
        type_name=exception_type.__name__,
        assertion=(
            snapshot_assertion_failure(exception)
            if isinstance(exception, AssertionFailure)
            else None
        ),
        cause=(
            _snapshot_exception(
                type(cause),
                cause,
                cause.__traceback__,
                depth=depth + 1,
                seen=seen,
            )
            if cause is not None
            else None
        ),
        context=(
            _snapshot_exception(
                type(context),
                context,
                context.__traceback__,
                depth=depth + 1,
                seen=seen,
            )
            if context is not None
            else None
        ),
        exceptions=tuple(
            _snapshot_exception(
                type(group_exception),
                group_exception,
                group_exception.__traceback__,
                depth=depth + 1,
                seen=seen,
            )
            for group_exception in grouped[:_MAX_EXCEPTION_GROUP_MEMBERS]
        ),
        notes=tuple(_safe_string(note) for note in getattr(exception, "__notes__", ())),
        suppress_context=exception.__suppress_context__,
    )


def snapshot_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> ExceptionDiagnostic:
    """Snapshot one exception and register its traceback for local debugging."""
    diagnostic = _snapshot_exception(
        exception_type,
        exception,
        traceback,
        depth=0,
        seen=set(),
    )
    if traceback is not None:
        if (store := _live_diagnostic_store.get()) is not None:
            store.tracebacks[id(diagnostic)] = traceback
        else:
            traceback_module.clear_frames(traceback)
    return diagnostic


@dataclass
class LiveDiagnosticStore:
    """Private run-local live state retained only until local PDB completes."""

    tracebacks: dict[int, TracebackType] = field(default_factory=dict)

    def traceback_for(self, diagnostic: ExceptionDiagnostic) -> TracebackType | None:
        return self.tracebacks.get(id(diagnostic))


_live_diagnostic_store: ContextVar[LiveDiagnosticStore | None] = ContextVar(
    "snektest_live_diagnostic_store", default=None
)


@contextmanager
def use_live_diagnostic_store(
    store: LiveDiagnosticStore,
) -> Generator[LiveDiagnosticStore]:
    """Retain live traceback state privately for one local execution run."""
    token = _live_diagnostic_store.set(store)
    try:
        yield store
    finally:
        _live_diagnostic_store.reset(token)
