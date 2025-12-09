import sys
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO


@contextmanager
def capture_output() -> Generator[tuple[StringIO, list[str]]]:
    """Context manager to capture stdout, stderr, and warnings."""
    output_buffer = StringIO()
    captured_warnings: list[str] = []
    system_stdout = sys.stdout
    system_stderr = sys.stderr

    sys.stdout = output_buffer
    sys.stderr = output_buffer

    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        try:
            yield output_buffer, captured_warnings
        finally:
            # Collect warnings
            for warning in warning_list:
                warning_msg = f"{warning.filename}:{warning.lineno}: {warning.category.__name__}: {warning.message}"
                captured_warnings.append(warning_msg)

            sys.stdout = system_stdout
            sys.stderr = system_stderr


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
