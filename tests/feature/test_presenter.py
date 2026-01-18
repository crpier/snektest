from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import TracebackType

from rich.console import Console

from snektest import assert_in, test
from snektest.models import (
    ErrorResult,
    FailedResult,
    PassedResult,
    TestName,
    TestResult,
)
from snektest.presenter.errors import print_failures
from snektest.presenter.summary import print_summary
from snektest.presenter.traceback import render_traceback


def _traceback_from_exception(exc: BaseException) -> TracebackType:
    try:
        raise exc
    except type(exc) as e:
        tb = e.__traceback__
        assert tb is not None
        return tb


@test()
def test_print_failures_includes_captured_and_fixture_teardown_output() -> None:
    console = Console(record=True)
    tb = _traceback_from_exception(RuntimeError("boom"))

    failing = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="t", params_part=""),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError, exc_value=RuntimeError("boom"), traceback=tb
        ),
        markers=(),
        captured_output=StringIO("captured"),
        fixture_teardown_failures=[],
        fixture_teardown_output="fixture-teardown-output",
        warnings=[],
    )

    print_failures(
        console,
        [failing],
        session_teardown_failures=None,
        session_teardown_output="session-out",
    )
    print_failures(
        console,
        [failing],
        session_teardown_failures=None,
        session_teardown_output=None,
    )

    text = console.export_text()
    assert_in("Captured output:", text)
    assert_in("Captured output from fixture teardowns:", text)
    assert_in("Output from session fixture teardowns", text)


@test()
def test_print_summary_error_without_message() -> None:
    console = Console(record=True)
    tb = _traceback_from_exception(RuntimeError("x"))

    result = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="e", params_part=""),
        duration=0.0,
        result=ErrorResult(
            exc_type=RuntimeError, exc_value=RuntimeError(""), traceback=tb
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [result], 0.0, session_teardown_failures=[])
    print_summary(console, [result], 0.0, session_teardown_failures=[])


@test()
def test_print_summary_warnings_and_failed_without_message() -> None:
    console = Console(record=True)

    passed = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="p", params_part=""),
        duration=0.0,
        result=PassedResult(),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=["warning"],
    )

    tb = _traceback_from_exception(RuntimeError("x"))
    failed = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="f", params_part=""),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError, exc_value=RuntimeError(""), traceback=tb
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [passed, failed], 0.0, session_teardown_failures=None)
    print_summary(console, [passed, failed], 0.0, session_teardown_failures=None)
    text = console.export_text()
    assert_in("WARNINGS", text)
    assert_in("SUMMARY", text)


@test()
def test_render_traceback_handles_non_traceback_object() -> None:
    console = Console(record=True)

    render_traceback(
        console,
        RuntimeError,
        RuntimeError("x"),
        object(),
        show_exception_line=False,
    )


@test()
def test_render_traceback_handles_oserror_open() -> None:
    console = Console(record=True)
    tb = _traceback_from_exception(RuntimeError("x"))

    def open_path(_: str) -> list[str]:
        raise OSError

    render_traceback(console, RuntimeError, RuntimeError("x"), tb, open_path=open_path)
    render_traceback(console, RuntimeError, RuntimeError("x"), tb)
    render_traceback(console, RuntimeError, RuntimeError("x"), tb)
    render_traceback(console, RuntimeError, RuntimeError("x"), tb)
    render_traceback(console, RuntimeError, RuntimeError("x"), tb)
