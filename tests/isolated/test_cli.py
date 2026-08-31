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
def test_parse_cli_args_timeout_defaults_to_sixty_seconds() -> None:
    options = parse_cli_args(["."])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.timeout, 60.0)


@test()
def test_parse_cli_args_no_timeout_disables_default() -> None:
    options = parse_cli_args(["--no-timeout", "."])
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
def test_parse_cli_args_timeout_rejects_no_timeout_conflict() -> None:
    result = parse_cli_args(["--timeout", "1", "--no-timeout"])
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
def test_parse_cli_args_benchmark_baseline() -> None:
    options = parse_cli_args(["--benchmark-baseline", "benchmarks.json", "."])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.benchmark_baseline, "benchmarks.json")
    assert_eq(options.update_benchmark_baseline, None)


@test()
def test_parse_cli_args_update_benchmark_baseline() -> None:
    options = parse_cli_args(["--update-benchmark-baseline", "benchmarks.json", "."])
    options = assert_isinstance(options, CliOptions)

    assert_eq(options.benchmark_baseline, None)
    assert_eq(options.update_benchmark_baseline, "benchmarks.json")


@test()
def test_parse_cli_args_rejects_baseline_mode_conflict() -> None:
    result = parse_cli_args(
        [
            "--benchmark-baseline",
            "old.json",
            "--update-benchmark-baseline",
            "new.json",
        ]
    )
    result = assert_isinstance(result, ParseError)

    assert_in("Only one --benchmark-baseline", result.message)


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
async def test_run_script_forwards_default_timeout() -> None:
    received_timeouts: list[object] = []

    async def record_timeout(*args: object, **kwargs: object) -> object:
        _ = args
        received_timeouts.append(kwargs["timeout"])
        raise asyncio.CancelledError

    _ = await run_script(["."], run_tests_programmatic_fn=record_timeout)

    assert_eq(received_timeouts, [60.0])


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


@test(mark="medium")
async def test_run_script_updates_and_compares_benchmark_baseline() -> None:
    """The non-interactive CLI can create and then enforce a baseline snapshot."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        temporary_path = Path(temporary_directory)
        test_file = temporary_path / "test_baseline_cli.py"
        baseline_path = temporary_path / "benchmarks.json"
        _ = test_file.write_text(
            """
from snektest import assert_benchmark, test


@test(mark="fast")
def test_timed_region() -> None:
    with assert_benchmark(
        name="region",
        median_below=1,
        median_regression_below=0.01,
        regression_noise_floor=1,
        rounds=1,
        warmup=0,
    ) as timing:
        for _ in timing.rounds:
            pass
""".lstrip()
        )

        update_output = StringIO()
        with contextlib.redirect_stdout(update_output):
            update_exit = await run_script(
                [
                    "--json-output",
                    "--update-benchmark-baseline",
                    str(baseline_path),
                    str(test_file),
                ]
            )

        compare_output = StringIO()
        with contextlib.redirect_stdout(compare_output):
            compare_exit = await run_script(
                [
                    "--json-output",
                    "--benchmark-baseline",
                    str(baseline_path),
                    str(test_file),
                ]
            )

    update_json = json.loads(update_output.getvalue())
    compare_json = json.loads(compare_output.getvalue())
    assert_eq(update_exit, 0)
    assert_eq(update_json["benchmark_baseline"]["written"], True)
    assert_eq(update_json["benchmark_baseline"]["updated_entries"], 1)
    assert_eq(compare_exit, 0)
    comparison = compare_json["tests"][0]["benchmark_measurements"][0][
        "baseline_comparison"
    ]
    assert_eq(comparison["verdict"], "passed")


@test(mark="medium")
async def test_json_baseline_error_is_machine_readable() -> None:
    """Malformed baseline errors do not leak Rich text into JSON mode."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        baseline_path = Path(temporary_directory) / "benchmarks.json"
        _ = baseline_path.write_text('{"schema_version": 2}')
        output = StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = await run_script(
                ["--json-output", "--benchmark-baseline", str(baseline_path), "."]
            )

    payload = json.loads(output.getvalue())
    assert_eq(exit_code, 2)
    assert_eq(payload["error"]["type"], "BadRequestError")
    assert_in("Invalid benchmark baseline", payload["error"]["message"])


@test(mark="medium")
async def test_update_error_does_not_print_success_summary() -> None:
    """Persistence must succeed before the console declares the run successful."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        temporary_path = Path(temporary_directory)
        test_file = temporary_path / "test_baseline_update_error.py"
        baseline_path = temporary_path / "benchmarks.json"
        _ = baseline_path.write_text('{"schema_version": 2}')
        _ = test_file.write_text(
            """
from snektest import assert_benchmark, test


@test(mark="fast")
def test_timed_region() -> None:
    with assert_benchmark(
        name="region",
        median_below=1,
        median_regression_below=0.10,
        rounds=1,
        warmup=0,
    ) as timing:
        for _ in timing.rounds:
            pass
""".lstrip()
        )
        output = StringIO()

        with contextlib.redirect_stdout(output), assert_raises(BadRequestError):
            _ = await run_script(
                [
                    "--update-benchmark-baseline",
                    str(baseline_path),
                    str(test_file),
                ]
            )

    rendered = output.getvalue()
    assert_in("OK", rendered)
    assert_eq("SUMMARY" in rendered, False)


@test(mark="medium")
async def test_overlapping_filters_execute_each_test_once() -> None:
    """Alternate filter spellings neither re-import nor duplicate a test."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        temporary_path = Path(temporary_directory)
        test_file = temporary_path / "test_overlapping_filters.py"
        import_count_file = temporary_path / "import-count.txt"
        _ = test_file.write_text(
            f"""
from pathlib import Path

from snektest import test

import_count_file = Path({str(import_count_file)!r})
import_count = int(import_count_file.read_text()) if import_count_file.exists() else 0
_ = import_count_file.write_text(str(import_count + 1))


@test(mark="fast")
def test_once() -> None:
    pass
""".lstrip()
        )
        relative_directory = temporary_path.relative_to(Path.cwd())

        summary = await run_tests_programmatic(
            [FilterItem(str(relative_directory)), FilterItem(str(test_file))]
        )
        import_count = int(import_count_file.read_text())

    assert_eq(summary.total_tests, 1)
    assert_eq(summary.passed, 1)
    assert_eq(import_count, 1)


@test(mark="medium")
async def test_failed_run_does_not_update_benchmark_baseline() -> None:
    """An intentional update remains atomic when any test fails."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        temporary_path = Path(temporary_directory)
        test_file = temporary_path / "test_failed_baseline_update.py"
        baseline_path = temporary_path / "benchmarks.json"
        _ = test_file.write_text(
            """
from snektest import assert_benchmark, fail, test


@test(mark="fast")
def test_timed_region() -> None:
    with assert_benchmark(
        name="region",
        median_below=1,
        median_regression_below=0.10,
        rounds=1,
        warmup=0,
    ) as timing:
        for _ in timing.rounds:
            pass
    fail("not accepted")
""".lstrip()
        )
        output = StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = await run_script(
                [
                    "--json-output",
                    "--update-benchmark-baseline",
                    str(baseline_path),
                    str(test_file),
                ]
            )

        baseline_exists = baseline_path.exists()

    payload = json.loads(output.getvalue())
    assert_eq(exit_code, 1)
    assert_eq(baseline_exists, False)
    assert_eq(payload["benchmark_baseline"]["written"], False)


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
