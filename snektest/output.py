"""Output capture with debugger-aware standard-stream proxies."""

from __future__ import annotations

import inspect
import os
import pdb  # noqa: T100
import sys
import warnings
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from tempfile import TemporaryFile
from typing import Any, BinaryIO, Self, TextIO, cast


class StdinProxy:
    """Proxy for sys.stdin that disables output capture when read from.

    This allows breakpoint()/pdb to work correctly even when output is being
    captured. When stdin is read from (e.g., pdb waiting for user input),
    the proxy calls a callback to restore stdout/stderr so the debugger
    can display its prompt and receive user input.
    """

    def __init__(
        self,
        original_stdin: TextIO,
        disable_capture_callback: Callable[[], None],
    ) -> None:
        self._original_stdin = original_stdin
        self._disable_capture = disable_capture_callback

    def read(self, size: int = -1) -> str:
        self._disable_capture()
        return self._original_stdin.read(size)

    def readline(self, size: int = -1) -> str:
        self._disable_capture()
        return self._original_stdin.readline(size)

    def readlines(self, hint: int = -1) -> list[str]:
        self._disable_capture()
        return self._original_stdin.readlines(hint)

    def __iter__(self) -> StdinProxy:
        self._disable_capture()
        return self

    def __next__(self) -> str:
        self._disable_capture()
        return next(self._original_stdin)

    def fileno(self) -> int:
        return self._original_stdin.fileno()

    def isatty(self) -> bool:
        return self._original_stdin.isatty()

    def close(self) -> None:
        pass  # Don't close the original stdin

    @property
    def closed(self) -> bool:
        return self._original_stdin.closed

    @property
    def encoding(self) -> str:
        return self._original_stdin.encoding

    @property
    def mode(self) -> str:
        return self._original_stdin.mode

    @property
    def name(self) -> str:
        return cast("str", self._original_stdin.name)

    def __getattr__(self, name: str) -> object:
        return getattr(self._original_stdin, name)


class DescriptorOutputCapture:
    """Temporarily quarantine writes to the process stdout descriptor.

    Python stream capture cannot intercept `os.write` or output inherited by a
    child process. This guard redirects descriptors 1 and 2 to a temporary file
    until the structured adapter is ready to write its single document.
    """

    def __init__(self) -> None:
        self._output_file: BinaryIO | None = None
        self._saved_stderr: int | None = None
        self._saved_stdout: int | None = None
        self._text: str | None = None

    def __enter__(self) -> Self:
        sys.stdout.flush()
        sys.stderr.flush()
        self._saved_stdout = os.dup(1)
        self._saved_stderr = os.dup(2)
        self._output_file = TemporaryFile(mode="w+b")
        os.dup2(self._output_file.fileno(), 1)
        os.dup2(self._output_file.fileno(), 2)
        return self

    def __exit__(self, *exc_info: object) -> None:
        _ = exc_info
        self.release()

    def release(self) -> str:
        """Restore stdout and return every byte written while quarantined."""
        if self._text is not None:
            return self._text
        output_file = self._output_file
        saved_stderr = self._saved_stderr
        saved_stdout = self._saved_stdout
        if output_file is None or saved_stderr is None or saved_stdout is None:
            return ""
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        output_file.seek(0)
        self._text = output_file.read().decode("utf-8", errors="replace")
        output_file.close()
        self._output_file = None
        self._saved_stderr = None
        self._saved_stdout = None
        return self._text


@dataclass(frozen=True)
class _OriginalSysState:
    stdout: Any
    stderr: Any
    stdin: Any
    settrace: Any
    breakpointhook: Any


def _make_disable_capture(
    *,
    system_stdout: Any,
    system_stderr: Any,
) -> Callable[[], None]:
    capture_disabled = False

    def disable_capture() -> None:
        nonlocal capture_disabled
        if capture_disabled:
            return
        sys.stdout = system_stdout
        sys.stderr = system_stderr
        capture_disabled = True

    return disable_capture


def _make_settrace_wrapper(
    *,
    system_settrace: Any,
    disable_capture: Callable[[], None],
) -> Callable[[Any], None]:
    def settrace_wrapper(func: Any) -> None:
        disable_capture()
        system_settrace(func)

    return settrace_wrapper


def _maybe_run_inline_pdb_breakpoint(
    *,
    system_breakpointhook: Any,
    caller_frame: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    pdb_factory: Callable[..., Any] = pdb.Pdb,
) -> Any:
    if system_breakpointhook is not sys.__breakpointhook__:
        return system_breakpointhook(*args, **kwargs)

    if caller_frame is None:
        return system_breakpointhook(*args, **kwargs)
    if args:
        return system_breakpointhook(*args, **kwargs)

    header = kwargs.get("header")
    commands = kwargs.get("commands")
    if set(kwargs) - {"header", "commands"}:
        return system_breakpointhook(*args, **kwargs)

    debugger = cast(
        "pdb.Pdb",
        pdb_factory(
            mode="inline",
            backend="monitoring",
            colorize=True,
        ),
    )
    if header is not None:
        _ = debugger.message(header)
    return debugger.set_trace(caller_frame, commands=commands)


def _make_breakpointhook_wrapper(
    *,
    system_breakpointhook: Any,
    disable_capture: Callable[[], None],
    frame_provider: Callable[[], Any] = inspect.currentframe,
    pdb_factory: Callable[..., Any] = pdb.Pdb,
) -> Callable[..., Any]:
    def breakpointhook_wrapper(*args: Any, **kwargs: Any) -> Any:
        disable_capture()
        sys.breakpointhook = system_breakpointhook

        frame = frame_provider()
        caller_frame = frame.f_back if frame else None

        return _maybe_run_inline_pdb_breakpoint(
            system_breakpointhook=system_breakpointhook,
            caller_frame=caller_frame,
            args=args,
            kwargs=kwargs,
            pdb_factory=pdb_factory,
        )

    return breakpointhook_wrapper


def _format_warnings(warnings_list: list[warnings.WarningMessage]) -> list[str]:
    return [
        f"{warning.filename}:{warning.lineno}: {warning.category.__name__}: {warning.message}"
        for warning in warnings_list
    ]


def _install_capture(
    *,
    output_buffer: StringIO,
    system_stdin: Any,
    disable_capture: Callable[[], None],
    settrace_wrapper: Any,
    breakpointhook_wrapper: Any,
) -> None:
    sys.stdout = output_buffer
    sys.stderr = output_buffer
    sys.stdin = StdinProxy(system_stdin, disable_capture)
    sys.settrace = settrace_wrapper
    sys.breakpointhook = breakpointhook_wrapper


def _restore_system_state(system: _OriginalSysState) -> None:
    sys.stdout = system.stdout
    sys.stderr = system.stderr
    sys.stdin = system.stdin
    sys.settrace = system.settrace
    sys.breakpointhook = system.breakpointhook


@contextmanager
def capture_output(
    *,
    frame_provider: Callable[[], Any] = inspect.currentframe,
    pdb_factory: Callable[..., Any] = pdb.Pdb,
) -> Generator[tuple[StringIO, list[str]]]:
    """Context manager to capture stdout, stderr, and warnings.

    If stdin is read from (e.g., by pdb/breakpoint), output capture is
    automatically disabled so the debugger can function properly.
    """
    output_buffer = StringIO()
    captured_warnings: list[str] = []

    original_sys = _OriginalSysState(
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=sys.stdin,
        settrace=sys.settrace,
        breakpointhook=sys.breakpointhook,
    )

    disable_capture = _make_disable_capture(
        system_stdout=original_sys.stdout,
        system_stderr=original_sys.stderr,
    )
    settrace_wrapper = _make_settrace_wrapper(
        system_settrace=original_sys.settrace,
        disable_capture=disable_capture,
    )
    breakpointhook_wrapper = _make_breakpointhook_wrapper(
        system_breakpointhook=original_sys.breakpointhook,
        disable_capture=disable_capture,
        frame_provider=frame_provider,
        pdb_factory=pdb_factory,
    )

    _install_capture(
        output_buffer=output_buffer,
        system_stdin=original_sys.stdin,
        disable_capture=disable_capture,
        settrace_wrapper=settrace_wrapper,
        breakpointhook_wrapper=breakpointhook_wrapper,
    )

    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        try:
            yield output_buffer, captured_warnings
        finally:
            captured_warnings.extend(_format_warnings(warning_list))
            _restore_system_state(original_sys)


@contextmanager
def maybe_capture_output(
    capture: bool,
) -> Generator[tuple[StringIO, list[str]]]:
    """Conditionally capture output based on a flag."""
    if capture:
        with capture_output() as (buffer, warnings_list):
            yield buffer, warnings_list
    else:
        buffer = StringIO()
        warnings_list: list[str] = []
        yield buffer, warnings_list
