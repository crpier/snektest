from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import TracebackType

from rich.console import Console

from snektest import assert_eq, assert_in, assert_is_not_none, assert_not_in, test
from snektest.models import (
    AssertionFailure,
    ErrorResult,
    FailedResult,
    PassedResult,
    TestName,
    TestResult,
)
from snektest.presenter import print_test_result_to_console
from snektest.presenter.diff import render_assertion_failure, render_dict_diff
from snektest.presenter.errors import print_failures
from snektest.presenter.summary import print_summary
from snektest.presenter.traceback import render_traceback


def _traceback_from_exception(exc: BaseException) -> TracebackType:
    try:
        raise exc
    except type(exc) as e:
        return assert_is_not_none(e.__traceback__)


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
def test_print_failures_separates_multiple_failed_tests_with_blank_line() -> None:
    console = Console(record=True)
    first_traceback = _traceback_from_exception(RuntimeError("one"))
    second_traceback = _traceback_from_exception(RuntimeError("two"))

    first_result = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="first", params_part=""),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError,
            exc_value=RuntimeError("one"),
            traceback=first_traceback,
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )
    second_result = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="second", params_part=""),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError,
            exc_value=RuntimeError("two"),
            traceback=second_traceback,
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_failures(console, [first_result, second_result])

    assert_in(
        "RuntimeError: one\n\nx.py::second ... FAIL (0.00s)",
        console.export_text(),
    )


@test()
def test_print_test_result_soft_wraps_long_names() -> None:
    console = Console(record=True, width=40)
    result = TestResult(
        name=TestName(
            file_path=Path("test_example_wrapping.py"),
            func_name="test_with_really_long_name_that_wont_fit_in_a_single_line",
            params_part="",
        ),
        duration=0.0,
        result=PassedResult(),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_test_result_to_console(console, result)

    text = console.export_text()
    assert_in(
        "test_with_really_long_name_that_wont_fit_in_a_single_line",
        text,
    )
    assert_in("OK (0.00s)", text)
    assert_eq(text.count("\n"), 1)


@test()
def test_print_failures_soft_wraps_long_names() -> None:
    console = Console(record=True, width=40)
    tb = _traceback_from_exception(RuntimeError("boom"))
    result = TestResult(
        name=TestName(
            file_path=Path("test_example_wrapping.py"),
            func_name="test_with_really_long_name_that_wont_fit_in_a_single_line",
            params_part="",
        ),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError, exc_value=RuntimeError("boom"), traceback=tb
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_failures(console, [result])

    text = console.export_text()
    assert_in(str(result.name), text)
    assert_in("FAIL (0.00s)", text)


@test()
def test_print_summary_soft_wraps_long_names() -> None:
    console = Console(record=True, width=40)
    tb = _traceback_from_exception(RuntimeError("boom"))
    result = TestResult(
        name=TestName(
            file_path=Path("test_example_wrapping.py"),
            func_name="test_with_really_long_name_that_wont_fit_in_a_single_line",
            params_part="",
        ),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError, exc_value=RuntimeError("boom"), traceback=tb
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [result], 0.0, session_teardown_failures=[])

    text = console.export_text()
    assert_in(str(result.name), text)
    assert_in("FAILED", text)


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
def test_print_summary_uses_one_line_exception_messages() -> None:
    console = Console(record=True)
    tb = _traceback_from_exception(RuntimeError("first line\nsecond line"))
    result = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="e", params_part=""),
        duration=0.0,
        result=ErrorResult(
            exc_type=RuntimeError,
            exc_value=RuntimeError("first line\nsecond line"),
            traceback=tb,
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [result], 0.0, session_teardown_failures=[])

    text = console.export_text()
    assert_in("ERROR x.py::e - RuntimeError: first line", text)
    assert_not_in("second line", text)


@test()
def test_print_summary_truncates_long_exception_messages() -> None:
    console = Console(record=True, width=80)
    long_message = "x" * 200
    tb = _traceback_from_exception(RuntimeError(long_message))
    result = TestResult(
        name=TestName(file_path=Path("x.py"), func_name="f", params_part=""),
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError,
            exc_value=RuntimeError(long_message),
            traceback=tb,
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [result], 0.0, session_teardown_failures=[])

    failed_lines = [
        line for line in console.export_text().splitlines() if line.startswith("FAILED")
    ]
    assert_eq(len(failed_lines), 1)
    assert_in("…", failed_lines[0])
    assert_not_in("x" * 100, failed_lines[0])


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


# --- Finding 1: markup injection in exception messages (traceback.py) ---
@test()
def test_render_traceback_preserves_markup_chars_in_exception_message() -> None:
    """A message containing Rich markup must render literally, not crash or vanish.

    `[/]` currently raises MarkupError (aborting the whole report) and `[bold red]`
    is silently swallowed, because the message is interpolated into a
    markup-enabled `console.print`.
    """
    console = Console(record=True, width=80)
    message = "boom [/] and [bold red] stay literal"
    tb = _traceback_from_exception(RuntimeError(message))

    render_traceback(console, RuntimeError, RuntimeError(message), tb)

    assert_in(message, console.export_text())


# --- Finding 5: long source lines cropped with no ellipsis (traceback.py) ---
@test()
def test_render_traceback_does_not_crop_long_source_line() -> None:
    """The failing source statement must remain fully visible (wrapped), not cropped."""
    console = Console(record=True, width=40)
    long_line = "    assert_eq(" + "a" * 60 + ', msg="TAIL_SENTINEL")'
    tb = _traceback_from_exception(RuntimeError("boom"))

    render_traceback(
        console,
        RuntimeError,
        RuntimeError("boom"),
        tb,
        show_exception_line=False,
        open_path=lambda _: [long_line] * 500,
    )

    assert_in("TAIL_SENTINEL", console.export_text())


# --- Finding 2: summary detail vanishes behind a long name (summary.py) ---
@test()
def test_print_summary_keeps_detail_when_name_is_long() -> None:
    """A short diagnostic must survive even when the test name is very long.

    Budgeting the detail against width-minus-name drives the remaining budget to
    <=1, collapsing every diagnostic to a bare `…` even at wide terminals.
    """
    console = Console(record=True, width=120)
    long_name = TestName(
        file_path=Path(
            "/home/user/projects/deeply/nested/package/subpackage/module/test_a_module.py"
        ),
        func_name="test_a_scenario_with_a_fairly_descriptive_and_long_function_name",
        params_part="",
    )
    tb = _traceback_from_exception(RuntimeError("boom"))
    result = TestResult(
        name=long_name,
        duration=0.0,
        result=FailedResult(
            exc_type=RuntimeError,
            exc_value=RuntimeError("SENTINEL_DETAIL"),
            traceback=tb,
        ),
        markers=(),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )

    print_summary(console, [result], 0.0, session_teardown_failures=[])

    assert_in("SENTINEL_DETAIL", console.export_text())


# --- Finding 4: diff ignores console width, hardcodes pprint width=80 (diff.py) ---
@test()
def test_render_dict_diff_uses_console_width() -> None:
    """Diffs must reflow to the actual terminal width, not a hardcoded 80 columns."""
    console = Console(record=True, width=120)
    actual = {f"key{i}": i for i in range(8)}
    expected = {f"key{i}": i + 1 for i in range(8)}

    render_dict_diff(console, actual, expected)

    # At width=120 this 88-char dict fits one line; hardcoded width=80 wraps it.
    one_line = "{'key0': 0, 'key1': 1, 'key2': 2, 'key3': 3, 'key4': 4, 'key5': 5, 'key6': 6, 'key7': 7}"
    assert_in(one_line, console.export_text())


# --- Finding 6: redundant repr blob above multiline string diff (diff.py) ---
@test()
def test_render_assertion_failure_omits_repr_blob_for_multiline_strings() -> None:
    """Multiline string failures show a clean per-line diff, not a `repr != repr` blob.

    The raw blob repeats both values on one line with literal `\\n` separators,
    duplicating the diff that follows.
    """
    console = Console(record=True, width=80)
    actual = "line one\nline two"
    expected = "line one\nline three"
    exc = AssertionFailure(
        f"{actual!r} != {expected!r}", actual=actual, expected=expected
    )

    render_assertion_failure(console, exc)

    text = console.export_text()
    assert_not_in("\\n", text)
    assert_in("line three", text)


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
