"""Explicit, process-local capture of standard-library log records."""

lazy import logging
lazy from dataclasses import dataclass, field
lazy from threading import RLock
lazy from types import TracebackType
lazy from typing import Self, override

lazy from snektest.models import BadRequestError


class _RecordingHandler(logging.Handler):
    def __init__(self, level: int) -> None:
        super().__init__(level)
        self.records: list[logging.LogRecord] = []
        self.active = False

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if self.active:
            self.records.append(record)

    def start(self) -> None:
        self.acquire()
        try:
            self.active = True
        finally:
            self.release()

    def stop(self) -> None:
        self.acquire()
        try:
            self.active = False
        finally:
            self.release()
        self.close()

    def snapshot(self) -> tuple[logging.LogRecord, ...]:
        self.acquire()
        try:
            return tuple(self.records)
        finally:
            self.release()


@dataclass
class _LoggerState:
    original_level: int
    handlers: set[_RecordingHandler] = field(default_factory=set)


_states: dict[logging.Logger, _LoggerState] = {}
_state_lock = RLock()


def _apply_level(logger: logging.Logger, state: _LoggerState) -> None:
    baseline = state.original_level
    if baseline == logging.NOTSET and logger.parent is not None:
        baseline = logger.parent.getEffectiveLevel()
    requested = min(
        (handler.level for handler in state.handlers if handler.level > logging.NOTSET),
        default=baseline,
    )
    level = requested if logging.NOTSET < requested < baseline else state.original_level
    if logger.level != level:
        logger.setLevel(level)


def _refresh_levels() -> None:
    # Parent changes can alter the inherited threshold of an active child capture.
    for logger in sorted(
        _states, key=lambda item: 0 if item.parent is None else item.name.count(".") + 1
    ):
        _apply_level(logger, _states[logger])


def _render_message(record: logging.LogRecord) -> str:
    try:
        return record.getMessage()
    except Exception:  # Rendering must not expose the exception or sensitive arguments.
        return "<log message unavailable>"


class LogCapture:
    """A context manager exposing captured records and rendered messages."""

    def __init__(
        self, logger: str | logging.Logger, *, level: int = logging.DEBUG
    ) -> None:
        if not isinstance(logger, str | logging.Logger):
            message = "Log capture requires an explicit logger name or logging.Logger."
            raise BadRequestError(message)
        if (
            isinstance(level, bool)
            or not isinstance(level, int)
            or level < logging.NOTSET
        ):
            message = "Log capture level must be a non-negative integer logging level."
            raise BadRequestError(message)
        self._logger = logging.getLogger(logger) if isinstance(logger, str) else logger
        self._handler = _RecordingHandler(level)
        self._used = False

    @property
    def records(self) -> tuple[logging.LogRecord, ...]:
        """Return a snapshot containing the original records in delivery order."""
        return self._handler.snapshot()

    @property
    def messages(self) -> tuple[str, ...]:
        """Render a snapshot of captured records."""
        return tuple(_render_message(record) for record in self.records)

    def __enter__(self) -> Self:
        with _state_lock:
            if self._used:
                message = "Log capture contexts are single-use; create a fresh capture_logs context."
                raise BadRequestError(message)
            self._used = True
            state = _states.setdefault(self._logger, _LoggerState(self._logger.level))
            state.handlers.add(self._handler)
            try:
                self._handler.start()
                self._logger.addHandler(self._handler)
                _refresh_levels()
            except BaseException:
                # Failed __enter__ does not get Python's automatic __exit__.
                self.__exit__(None, None, None)
                raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with _state_lock:
            self._handler.stop()
            self._logger.removeHandler(self._handler)
            state = _states[self._logger]
            state.handlers.remove(self._handler)
            if not state.handlers:
                del _states[self._logger]
            try:
                if not state.handlers and self._logger.level != state.original_level:
                    self._logger.setLevel(state.original_level)
            finally:
                _refresh_levels()


def capture_logs(
    logger: str | logging.Logger, *, level: int = logging.DEBUG
) -> LogCapture:
    """Capture one logger hierarchy without removing application handlers."""
    return LogCapture(logger, level=level)
