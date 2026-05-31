from __future__ import annotations

import asyncio
import contextlib
import json
import runpy
import sys
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import cast

from snektest import assert_eq, assert_raises, test
from snektest.cli import (
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
def test_parse_cli_args_invalid_option_returns_2() -> None:
    result = parse_cli_args(["--nope"])
    assert_eq(result, 2)


@test()
def test_parse_cli_args_defaults_to_dot() -> None:
    parsed = parse_cli_args([])
    assert not isinstance(parsed, int)

    potential_filter, options = parsed
    assert_eq(potential_filter, ["."])
    assert_eq(options.action, None)
    assert_eq(options.capture_output, True)
    assert_eq(options.json_output, False)
    assert_eq(options.pdb_on_failure, False)
    assert_eq(options.mark, None)


@test()
def test_parse_cli_args_s_flag_disables_capture() -> None:
    parsed = parse_cli_args(["-s", "."])
    assert not isinstance(parsed, int)

    _potential_filter, options = parsed
    assert_eq(options.capture_output, False)
    assert_eq(options.mark, None)


@test()
def test_parse_cli_args_agent_docs_action() -> None:
    parsed = parse_cli_args(["--agent-docs"])
    assert not isinstance(parsed, int)

    potential_filter, options = parsed
    assert_eq(potential_filter, [])
    assert_eq(options.action, "agent_docs")


@test()
def test_parse_cli_args_example_command_action() -> None:
    parsed = parse_cli_args(["example", "async"])
    assert not isinstance(parsed, int)

    potential_filter, options = parsed
    assert_eq(potential_filter, [])
    assert_eq(options.action, "show_example")
    assert_eq(options.example_name, "async")


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
    assert "snektest agent guide" in buffer.getvalue()


@test()
async def test_run_script_prints_help_with_agent_docs_option() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--help"])

    assert_eq(result, 0)
    assert "--agent-docs" in buffer.getvalue()


@test()
async def test_run_script_lists_examples() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--examples"])

    assert_eq(result, 0)
    assert "basic" in buffer.getvalue()
    assert "async" in buffer.getvalue()


@test()
async def test_run_script_prints_named_example() -> None:
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        result = await run_script(["--example", "fixtures"])

    assert_eq(result, 0)
    assert "@session_fixture()" in buffer.getvalue()


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
def test_run_module_main_invokes_cli() -> None:
    original_argv = list(sys.argv)
    try:
        sys.argv = ["snektest", "--nope"]
        with assert_raises(SystemExit):
            _ = runpy.run_module("snektest.cli", run_name="__main__")
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
