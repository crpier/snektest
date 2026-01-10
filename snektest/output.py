import inspect
import pdb  # noqa: T100
import sys
import warnings
from collections.abc import Callable, Generator
from contextlib import contextmanager
from io import StringIO
from typing import Any, TextIO


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
        return self._original_stdin.name

    def __getattr__(self, name: str) -> object:
        # Delegate unknown attributes to original stdin
        return getattr(self._original_stdin, name)


@contextmanager
def capture_output() -> Generator[tuple[StringIO, list[str]]]:  # noqa: PLR0915, C901
    """Context manager to capture stdout, stderr, and warnings.

    If stdin is read from (e.g., by pdb/breakpoint), output capture is
    automatically disabled so the debugger can function properly.
    """
    output_buffer = StringIO()
    captured_warnings: list[str] = []
    system_stdout = sys.stdout
    system_stderr = sys.stderr
    system_stdin = sys.stdin
    system_settrace = sys.settrace
    system_breakpointhook = sys.breakpointhook

    capture_disabled = False

    def disable_capture() -> None:
        nonlocal capture_disabled
        if not capture_disabled:
            sys.stdout = system_stdout
            sys.stderr = system_stderr
            capture_disabled = True

    def settrace_wrapper(func: Any) -> None:
        """Wrapper for sys.settrace that disables capture when debugger starts."""
        # When pdb/breakpoint calls settrace, restore stdout/stderr first
        disable_capture()
        # Then call the original settrace
        system_settrace(func)

    def breakpointhook_wrapper(*args: Any, **kwargs: Any) -> Any:
        """Wrapper for sys.breakpointhook that disables capture before pdb init."""
        # Restore stdout/stderr BEFORE pdb.__init__ captures them
        # This must happen immediately so pdb sees the correct stdout/stderr
        disable_capture()
        # Restore the original breakpointhook so subsequent breakpoint() calls behave
        sys.breakpointhook = system_breakpointhook
        if system_breakpointhook is sys.__breakpointhook__:
            frame = inspect.currentframe()
            caller_frame = frame.f_back if frame else None
            if caller_frame is None:
                return system_breakpointhook(*args, **kwargs)
            if args:
                return system_breakpointhook(*args, **kwargs)
            header = kwargs.get("header")
            commands = kwargs.get("commands")
            if set(kwargs) - {"header", "commands"}:
                return system_breakpointhook(*args, **kwargs)

            debugger: pdb.Pdb = pdb.Pdb(
                mode="inline",
                backend="monitoring",
                colorize=True,
            )
            if header is not None:
                _ = debugger.message(header)
            return debugger.set_trace(caller_frame, commands=commands)
        return system_breakpointhook(*args, **kwargs)

    sys.stdout = output_buffer
    sys.stderr = output_buffer
    sys.stdin = StdinProxy(system_stdin, disable_capture)
    sys.settrace = settrace_wrapper
    sys.breakpointhook = breakpointhook_wrapper

    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        try:
            yield output_buffer, captured_warnings
        finally:
            # Collect warnings
            for warning in warning_list:
                warning_msg = f"{warning.filename}:{warning.lineno}: {warning.category.__name__}: {warning.message}"
                captured_warnings.append(warning_msg)

            # Restore all streams (stdout/stderr may already be restored if
            # capture was disabled by stdin read or breakpoint)
            sys.stdout = system_stdout
            sys.stderr = system_stderr
            sys.stdin = system_stdin
            sys.settrace = system_settrace
            sys.breakpointhook = system_breakpointhook


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
