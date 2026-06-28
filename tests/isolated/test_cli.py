from __future__ import annotations

import asyncio
import contextlib
import json
import runpy
import sys
import tempfile
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import cast

from snektest import (
    assert_eq,
    assert_in,
    assert_is_none,
    assert_isinstance,
    assert_raises,
    test,
)
from snektest.cli import (
    CliOptions,
    ParseError,
    main,
    main_inner,
    parse_cli_args,
    run_script,
    run_tests_programmatic,
)
from snektest.models import (
    BadRequestError,
    CollectionError,
    FilterItem,
    PassedResult,
    TestName,
    TestResult,
    UnreachableError,
)


@test()
def test_parse_cli_args_invalid_option_returns_error() -> None:
    result = parse_cli_args(["--nope"])
    result = assert_isinstance(result, ParseError)
    assert_in("Invalid option", result.message)


@test()
def test_parse_cli_args_defaults_to_dot() -> None:
    options = parse_cli_args([])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.filters, (".",))
    assert_eq(options.action, None)
    assert_eq(options.capture_output, True)
    assert_eq(options.json_output, False)
    assert_eq(options.pdb_on_failure, False)
    assert_eq(options.mark, None)


@test()
def test_parse_cli_args_timeout_flag_parses_seconds() -> None:
    options = parse_cli_args(["--timeout", "1.5", "."])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.timeout, 1.5)


@test()
def test_parse_cli_args_timeout_defaults_to_none() -> None:
    options = parse_cli_args(["."])
    options = assert_isinstance(options, CliOptions)

    assert_is_none(options.timeout)


@test()
def test_parse_cli_args_timeout_rejects_non_numeric() -> None:
    result = parse_cli_args(["--timeout", "abc"])
    result = assert_isinstance(result, ParseError)
    assert_in("Expected seconds", result.message)


@test()
def test_parse_cli_args_timeout_rejects_non_positive() -> None:
    result = parse_cli_args(["--timeout", "0"])
    result = assert_isinstance(result, ParseError)
    assert_in("Must be positive", result.message)


@test()
def test_parse_cli_args_timeout_rejects_repeats() -> None:
    result = parse_cli_args(["--timeout", "1", "--timeout", "2"])
    result = assert_isinstance(result, ParseError)
    assert_in("Only one --timeout", result.message)


@test()
def test_parse_cli_args_timeout_requires_value() -> None:
    result = parse_cli_args(["--timeout"])
    result = assert_isinstance(result, ParseError)
    assert_in("Missing value for --timeout", result.message)


@test()
def test_parse_cli_args_s_flag_disables_capture() -> None:
    options = parse_cli_args(["-s", "."])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.capture_output, False)
    assert_eq(options.mark, None)


@test()
def test_parse_cli_args_agent_docs_action() -> None:
    options = parse_cli_args(["--agent-docs"])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.filters, ())
    assert_eq(options.action, "agent_docs")


@test()
def test_parse_cli_args_example_command_action() -> None:
    options = parse_cli_args(["example", "async"])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.filters, ())
    assert_eq(options.action, "show_example")
    assert_eq(options.example_name, "async")


@test()
def test_parse_cli_args_duplicate_action_returns_error() -> None:
    result = parse_cli_args(["--help", "--examples"])
    result = assert_isinstance(result, ParseError)
    assert_in("Only one help/docs/examples", result.message)


@test()
def test_parse_cli_args_action_with_filter_returns_error() -> None:
    result = parse_cli_args(["--help", "."])
    result = assert_isinstance(result, ParseError)
    assert_in("Cannot combine", result.message)


@test()
async def test_run_script_returns_2_on_args_error() -> None:
    result = await run_script(["does-not-exist"])
    assert_eq(result, 2)


@test()
async def test_run_script_returns_parse_cli_args_exit_code() -> None:
    result = await run_script(["--nope"])
    assert_eq(result, 2)


@test()
async def test_run_script_prints_agent_docs() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--llms"])

    assert_eq(result, 0)
    assert_in("snektest agent guide", buffer.getvalue())


@test()
async def test_run_script_prints_help_with_agent_docs_option() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--help"])

    assert_eq(result, 0)
    assert_in("--agent-docs", buffer.getvalue())


@test()
async def test_run_script_lists_examples() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--examples"])

    assert_eq(result, 0)
    assert_in("basic", buffer.getvalue())
    assert_in("async", buffer.getvalue())


@test()
async def test_run_script_prints_named_example() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--example", "fixtures"])

    assert_eq(result, 0)
    assert_in('@fixture(scope="session")', buffer.getvalue())


@test()
async def test_run_script_rejects_unknown_example() -> None:
    result = await run_script(["--example", "missing"])
    assert_eq(result, 2)


@test()
async def test_run_script_returns_2_on_cancelled_error() -> None:
    async def raise_cancelled(*args: object, **kwargs: object) -> object:
        _ = (args, kwargs)
        raise asyncio.CancelledError

    result = await run_script(["."], run_tests_programmatic_fn=raise_cancelled)
    assert_eq(result, 2)


@test()
async def test_run_tests_programmatic_rejects_unknown_marker() -> None:
    with assert_raises(BadRequestError):
        _ = await run_tests_programmatic([FilterItem(".")], mark="needs-s3")


@test()
async def test_run_tests_programmatic_rejects_missing_explicit_test_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_missing_explicit_name.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )

        with assert_raises(CollectionError) as raised:
            _ = await run_tests_programmatic([FilterItem(f"{test_file}::aaa")])

    assert_in("No test named `aaa`", str(raised.exception))


@test()
async def test_run_script_json_output_is_machine_readable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_json_output.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )

        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            result = await run_script(["--json-output", str(test_file)])

    assert_eq(result, 0)
    assert_eq(json.loads(buffer.getvalue())["passed"], 1)


@test()
async def test_run_tests_programmatic_does_not_print() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_programmatic_output.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )

        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            summary = await run_tests_programmatic([FilterItem(str(test_file))])

    assert_eq(summary.passed, 1)
    assert_eq(buffer.getvalue(), "")


@test()
async def test_run_script_json_output_includes_markers() -> None:
    async def fake_run(*args: object, **kwargs: object) -> object:
        _ = (args, kwargs)
        test_result = TestResult(
            name=TestName(
                file_path=Path("tests/test_fake.py"), func_name="t", params_part=""
            ),
            duration=0.0,
            result=PassedResult(),
            markers=("fast",),
            captured_output=StringIO(""),
            fixture_teardown_failures=[],
            fixture_teardown_output=None,
            warnings=[],
        )
        return type(
            "Summary",
            (),
            {
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "fixture_teardown_failed": 0,
                "session_teardown_failed": 0,
                "session_teardown_failures": [],
                "test_results": [test_result],
            },
        )()

    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(
            ["--json-output"],
            run_tests_programmatic_fn=fake_run,
        )
    assert_eq(result, 0)
    payload = json.loads(buffer.getvalue())
    assert_eq(payload["tests"][0]["markers"], ["fast"])


@test()
def test_main_runs_and_exits() -> None:
    original_argv = list(sys.argv)
    try:
        sys.argv = ["snektest", "--nope"]
        with assert_raises(SystemExit):
            main()
    finally:
        sys.argv = original_argv


@test()
def test_run_path_main_invokes_cli() -> None:
    original_argv = list(sys.argv)
    try:
        sys.argv = ["snektest", "--nope"]
        with assert_raises(SystemExit):
            _ = runpy.run_path(str(Path("snektest/cli.py")), run_name="__main__")
    finally:
        sys.argv = original_argv


@test()
def test_main_exit_paths() -> None:
    def assert_exit_code(exc: BaseException | None, expected: int) -> None:
        def fake_run(coro: object) -> int:
            closer_obj = getattr(coro, "close", None)
            if callable(closer_obj):
                closer = cast("Callable[[], None]", closer_obj)
                closer()
            if exc is not None:
                raise exc
            return 0

        result = main_inner(async_runner=fake_run, argv=["."])
        assert_eq(result, expected)
        result = main_inner(async_runner=fake_run, argv=["."])
        assert_eq(result, expected)
        result = main_inner(async_runner=fake_run, argv=["."])
        assert_eq(result, expected)

    assert_exit_code(None, 0)
    assert_exit_code(CollectionError("x"), 2)
    assert_exit_code(BadRequestError("x"), 2)
    assert_exit_code(UnreachableError("x"), 2)
    assert_exit_code(KeyboardInterrupt(), 2)
    assert_exit_code(RuntimeError("x"), 1)
