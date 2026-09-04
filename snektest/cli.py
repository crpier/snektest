import asyncio
import json
import sys
import traceback
from collections.abc import Callable, Coroutine
from contextlib import nullcontext
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal, cast

from snektest._version import __version__
from snektest.agent_docs import (
    get_agent_docs,
    get_example_source,
    get_examples_listing,
)
from snektest.benchmark_baseline import BenchmarkBaseline, discover_project_root
from snektest.collection import collect_test_plan
from snektest.diagnostics import snapshot_exception
from snektest.execution import run_tests
from snektest.junit import build_junit_xml
from snektest.models import (
    ArgsError,
    BadRequestError,
    BenchmarkBaselineRun,
    CollectionDiagnostics,
    CollectionError,
    FilterItem,
    RunInfrastructureError,
    RunResult,
    UnreachableError,
)
from snektest.output import DescriptorOutputCapture
from snektest.parallel import run_tests_parallel
from snektest.presenter import print_error
from snektest.reporting import (
    ConsoleRunReporter,
    DeferredRunReporter,
    NullRunReporter,
    RunReporter,
)
from snektest.structured import (
    build_json_error as _json_error_document,
)
from snektest.structured import (
    build_json_summary,
)


def _baseline_cli_error(error: BadRequestError, *, json_output: bool) -> int:
    """Keep baseline configuration errors machine-readable in JSON mode."""
    if json_output:
        print(
            json.dumps(
                _json_error_document(
                    category="configuration",
                    exception=snapshot_exception(
                        type(error), error, error.__traceback__
                    ),
                    exit_code=2,
                    message=str(error),
                    type_name=type(error).__name__,
                )
            )
        )
        return 2
    raise error


TestRunSummary = RunResult
"""Compatibility name for the normalized programmatic run result."""


type CliAction = Literal[
    "agent_docs", "help", "list_examples", "show_example", "version"
]
type WorkerCount = int | Literal["auto"]

_DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CliOptions:
    action: CliAction | None = None
    allow_empty: bool = False
    benchmark_baseline: str | None = None
    capture_output: bool = True
    example_name: str | None = None
    filters: tuple[str, ...] = ()
    json_output: bool = False
    junit_output: str | None = None
    pdb_on_failure: bool = False
    mark: str | None = None
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS
    update_benchmark_baseline: str | None = None
    workers: WorkerCount | None = None


@dataclass(frozen=True)
class ParseError:
    """A CLI usage error, returned by the parser so the caller renders it once.

    Parsing stays pure: every invalid-argument path returns one of these instead
    of printing, so the single render seam lives in `run_script`.
    """

    message: str


VALID_MARKER_VALUES = {"fast", "medium", "slow"}

HELP_TEXT = """Usage: snektest [OPTIONS] [FILTER ...]

Run snektest tests.

Filters:
  .                                      Run tests below current directory
  tests/test_file.py                     Run one test file
  tests/test_file.py::test_name          Run one test function
  tests/test_file.py::test_name[param]   Run one parameterized case

Options:
  -h, --help        Show this help message
  --version         Show the installed snektest version
  -s                Disable stdout/stderr capture
  --agent-docs      Print AI-agent usage guide
  --llms            Alias for --agent-docs
  --examples        List bundled examples
  --example NAME    Print a bundled example
  --json-output     Print one versioned JSON document to stdout
  --junit-output PATH
                    Write a JUnit XML report
  --allow-empty     Exit successfully when no tests are selected
  --benchmark-baseline PATH
                    Compare opted-in benchmarks with a machine-bound baseline
  --update-benchmark-baseline PATH
                    Atomically update opted-in benchmarks after a passing run
  --mark MARK       Run tests marked fast, medium, or slow; marking tests is recommended
  -n, --workers N   Run in N worker processes, or use auto
  --timeout SECONDS Set a finite positive async-test timeout (default: 60)
  --no-timeout      Disable async-test body timeout; cleanup remains bounded
  --pdb             Drop into post-mortem debugger on first failure

Example commands:
  snektest --agent-docs
  snektest --examples
  snektest --example async
  snektest examples
  snektest example async
  python -m snektest --agent-docs
"""


def _is_valid_mark_value(mark_value: str) -> bool:
    return mark_value in VALID_MARKER_VALUES


def _invalid_mark_message(mark_value: str) -> str:
    allowed = ", ".join(sorted(VALID_MARKER_VALUES))
    return f"Invalid --mark value: `{mark_value}`. Use one of: {allowed}"


def _consume_flag_value(
    argv: list[str], index: int, flag: str
) -> tuple[str, int] | ParseError:
    """Read the value following a value-taking flag at `index`.

    Returns the value with the index it was consumed from, or a ParseError when
    the value is missing or looks like another option (a leading dash).
    """
    if index + 1 >= len(argv):
        return ParseError(f"Missing value for {flag}")
    value = argv[index + 1]
    if value.startswith("-"):
        return ParseError(f"Missing value for {flag}")
    return value, index + 1


# Args that select a CLI action, both as `--flags` and as bare positional words.
_ACTION_ARGS: dict[str, CliAction] = {
    "--agent-docs": "agent_docs",
    "--examples": "list_examples",
    "--help": "help",
    "--llms": "agent_docs",
    "--version": "version",
    "-h": "help",
    "examples": "list_examples",
}


def _parse_mark_flag(
    argv: list[str], index: int, current_mark: str | None
) -> tuple[str, int] | ParseError:
    """Parse `--mark` and its value, rejecting repeats and unknown markers.

    Returns the marker with the index its value was consumed from, or a
    ParseError. Lives apart from the main loop so the dispatch stays flat.
    """
    if current_mark is not None:
        return ParseError("Only one --mark value is supported")
    consumed = _consume_flag_value(argv, index, "--mark")
    if isinstance(consumed, ParseError):
        return consumed
    mark_value, value_index = consumed
    if not _is_valid_mark_value(mark_value):
        return ParseError(_invalid_mark_message(mark_value))
    return mark_value, value_index


def _parse_timeout_flag(
    argv: list[str], index: int, *, timeout_option_seen: bool
) -> tuple[float, int] | ParseError:
    """Parse `--timeout`, rejecting repeats and non-finite or non-positive values.

    Returns the timeout in seconds with the index its value was consumed from, or
    a ParseError.
    """
    if timeout_option_seen:
        return ParseError("Only one --timeout or --no-timeout option is supported")
    consumed = _consume_flag_value(argv, index, "--timeout")
    if isinstance(consumed, ParseError):
        return consumed
    raw_value, value_index = consumed
    try:
        timeout = float(raw_value)
    except ValueError:
        return ParseError(f"Invalid --timeout value: `{raw_value}`. Expected seconds.")
    if not isfinite(timeout):
        return ParseError(f"Invalid --timeout value: `{raw_value}`. Must be finite.")
    if timeout <= 0:
        return ParseError(f"Invalid --timeout value: `{raw_value}`. Must be positive.")
    return timeout, value_index


def _parse_workers_flag(
    argv: list[str], index: int, *, workers_option_seen: bool
) -> tuple[WorkerCount, int] | ParseError:
    """Parse one positive worker count or the exact value `auto`."""
    if workers_option_seen:
        return ParseError("Only one -n or --workers option is supported")
    consumed = _consume_flag_value(argv, index, argv[index])
    if isinstance(consumed, ParseError):
        return consumed
    raw_value, value_index = consumed
    if raw_value == "auto":
        return "auto", value_index
    try:
        workers = int(raw_value)
    except ValueError:
        return ParseError(
            f"Invalid --workers value: `{raw_value}`. Expected a positive integer or auto."
        )
    if workers <= 0:
        return ParseError(
            f"Invalid --workers value: `{raw_value}`. Must be a positive integer."
        )
    return workers, value_index


def _print_cli_action(options: CliOptions) -> int:
    output = ""
    if options.action == "help":
        output = HELP_TEXT
    elif options.action == "agent_docs":
        output = get_agent_docs()
    elif options.action == "version":
        output = f"snektest {__version__}\n"
    elif options.action == "list_examples":
        output = get_examples_listing()
    elif options.action == "show_example":
        if options.example_name is None:
            print_error("Missing example name")
            return 2
        try:
            output = get_example_source(options.example_name)
        except BadRequestError as e:
            print_error(str(e))
            return 2
    print(output, end="")
    return 0


def parse_cli_args(  # noqa: C901, PLR0911, PLR0912, PLR0915
    argv: list[str],
) -> CliOptions | ParseError:
    """Parse argv into CliOptions, or a ParseError on invalid usage.

    Pure: never prints. The caller renders any ParseError once. Value-taking
    flags (`--mark`, `--example`) consume the following arg; positional
    `example`/`examples` are action words, and every other bare arg is a
    filter.

    The flat per-flag dispatch is intentionally branchy: it is the body of a
    deep module behind a one-call interface. Splitting it to satisfy the
    complexity metric would only re-spread parsing state across helpers.
    """
    action: CliAction | None = None
    allow_empty = False
    benchmark_baseline: str | None = None
    capture_output = True
    example_name: str | None = None
    json_output = False
    junit_output: str | None = None
    mark: str | None = None
    pdb_on_failure = False
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS
    timeout_option_seen = False
    update_benchmark_baseline: str | None = None
    workers: WorkerCount | None = None
    workers_option_seen = False
    filters: list[str] = []
    duplicate_action = ParseError("Only one informational command is supported")

    index = 0
    while index < len(argv):
        arg = argv[index]
        chosen_action = "show_example" if arg in {"--example", "example"} else None
        chosen_action = chosen_action or _ACTION_ARGS.get(arg)
        if chosen_action is not None:
            if action is not None:
                return duplicate_action
            action = chosen_action
            if chosen_action == "show_example":
                consumed = _consume_flag_value(argv, index, "--example")
                if isinstance(consumed, ParseError):
                    return consumed
                example_name, index = consumed
        elif arg == "-s":
            capture_output = False
        elif arg == "--json-output":
            json_output = True
        elif arg == "--junit-output":
            if junit_output is not None:
                return ParseError("Only one --junit-output value is supported")
            consumed = _consume_flag_value(argv, index, arg)
            if isinstance(consumed, ParseError):
                return consumed
            junit_output, index = consumed
        elif arg == "--allow-empty":
            allow_empty = True
        elif arg in {"--benchmark-baseline", "--update-benchmark-baseline"}:
            if benchmark_baseline is not None or update_benchmark_baseline is not None:
                return ParseError(
                    "Only one --benchmark-baseline or --update-benchmark-baseline option is supported"
                )
            consumed = _consume_flag_value(argv, index, arg)
            if isinstance(consumed, ParseError):
                return consumed
            baseline_path, index = consumed
            if arg == "--benchmark-baseline":
                benchmark_baseline = baseline_path
            else:
                update_benchmark_baseline = baseline_path
        elif arg == "--pdb":
            pdb_on_failure = True
        elif arg == "--mark":
            parsed_mark = _parse_mark_flag(argv, index, mark)
            if isinstance(parsed_mark, ParseError):
                return parsed_mark
            mark, index = parsed_mark
        elif arg == "--timeout":
            parsed_timeout = _parse_timeout_flag(
                argv, index, timeout_option_seen=timeout_option_seen
            )
            if isinstance(parsed_timeout, ParseError):
                return parsed_timeout
            timeout, index = parsed_timeout
            timeout_option_seen = True
        elif arg == "--no-timeout":
            if timeout_option_seen:
                return ParseError(
                    "Only one --timeout or --no-timeout option is supported"
                )
            timeout = None
            timeout_option_seen = True
        elif arg in {"-n", "--workers"}:
            parsed_workers = _parse_workers_flag(
                argv, index, workers_option_seen=workers_option_seen
            )
            if isinstance(parsed_workers, ParseError):
                return parsed_workers
            workers, index = parsed_workers
            workers_option_seen = True
        elif arg.startswith("-"):
            return ParseError(f"Invalid option: `{arg}`")
        else:
            filters.append(arg)
        index += 1

    if action is not None and json_output:
        return ParseError("Cannot combine informational commands with --json-output")
    if action is not None and (
        filters
        or benchmark_baseline is not None
        or junit_output is not None
        or update_benchmark_baseline is not None
    ):
        return ParseError(
            "Cannot combine informational commands with test filters or baseline options"
        )
    if pdb_on_failure and json_output:
        return ParseError("Cannot combine --pdb with --json-output")
    if pdb_on_failure and workers is not None:
        return ParseError(
            "Cannot combine --pdb with --workers; rerun without --workers to debug locally"
        )
    if action is None and not filters:
        filters.append(".")

    return CliOptions(
        action=action,
        allow_empty=allow_empty,
        benchmark_baseline=benchmark_baseline,
        capture_output=capture_output,
        example_name=example_name,
        filters=tuple(filters),
        json_output=json_output,
        junit_output=junit_output,
        mark=mark,
        pdb_on_failure=pdb_on_failure,
        timeout=timeout,
        update_benchmark_baseline=update_benchmark_baseline,
        workers=workers,
    )


async def _run_tests_with_collected_plan(  # noqa: PLR0913
    filter_items: list[FilterItem],
    *,
    allow_empty: bool,
    capture_output: bool,
    pdb_on_failure: bool,
    mark: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109
    reporter: RunReporter | None = None,
    benchmark_baseline: BenchmarkBaseline | None = None,
) -> RunResult:
    collection_diagnostics = CollectionDiagnostics()
    try:
        test_cases = await asyncio.to_thread(
            collect_test_plan,
            filter_items,
            allow_empty=allow_empty,
            capture_output=capture_output,
            diagnostics=collection_diagnostics,
            mark=mark,
        )
    except CollectionError as error:
        error.collection_output = collection_diagnostics.output
        error.collection_warnings = collection_diagnostics.warnings
        raise
    return await run_tests(
        test_cases,
        capture_output=capture_output,
        collection_output=collection_diagnostics.output,
        collection_warnings=collection_diagnostics.warnings,
        pdb_on_failure=pdb_on_failure,
        timeout=timeout,
        reporter=reporter,
        benchmark_baseline=benchmark_baseline,
    )


def exit_code_from_summary(summary: RunResult) -> int:
    return summary.exit_code


async def run_tests_programmatic(  # noqa: PLR0913
    filter_items: list[FilterItem],
    *,
    allow_empty: bool = False,
    capture_output: bool = True,
    pdb_on_failure: bool = False,
    mark: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109
    reporter: RunReporter | None = None,
    benchmark_baseline: BenchmarkBaseline | None = None,
    workers: WorkerCount | None = None,
) -> RunResult:
    """Run tests and return structured results instead of printing.

    This is the programmatic API for testing snektest itself.
    Returns structured data instead of printing by default.

    Args:
        filter_items: List of filter items to run tests from
        capture_output: Whether to capture test output
        reporter: Optional progress reporter. Defaults to no presentation side effects.

    Returns:
        RunResult with test results and normalized counts
    """
    if mark is not None and not _is_valid_mark_value(mark):
        raise BadRequestError(_invalid_mark_message(mark))
    if workers is not None and (
        isinstance(workers, bool)
        or not (workers == "auto" or isinstance(workers, int))
        or (isinstance(workers, int) and workers <= 0)
    ):
        msg = "workers must be a positive integer, 'auto', or None"
        raise BadRequestError(msg)

    selected_reporter = reporter or NullRunReporter()
    if workers is None:
        return await _run_tests_with_collected_plan(
            filter_items,
            allow_empty=allow_empty,
            capture_output=capture_output,
            pdb_on_failure=pdb_on_failure,
            mark=mark,
            timeout=timeout,
            reporter=selected_reporter,
            benchmark_baseline=benchmark_baseline,
        )
    if pdb_on_failure:
        msg = "Cannot combine pdb_on_failure with workers"
        raise BadRequestError(msg)
    return await run_tests_parallel(
        filter_items,
        allow_empty=allow_empty,
        capture_output=capture_output,
        mark=mark,
        reporter=selected_reporter,
        timeout=timeout,
        workers=workers,
        benchmark_baseline=benchmark_baseline,
    )


async def run_script(  # noqa: C901, PLR0911, PLR0912, PLR0915
    argv: list[str] | None = None,
    *,
    run_tests_programmatic_fn: Callable[..., Coroutine[object, object, object]]
    | None = None,
) -> int:
    """Parse arguments and run tests."""
    arguments = sys.argv[1:] if argv is None else argv
    json_requested = "--json-output" in arguments
    parsed = parse_cli_args(arguments)
    if isinstance(parsed, ParseError):
        if json_requested:
            print(
                json.dumps(
                    _json_error_document(
                        category="usage",
                        exit_code=2,
                        message=parsed.message,
                        type_name="ParseError",
                    )
                )
            )
        else:
            print_error(parsed.message)
        return 2

    options = parsed

    if options.action is not None:
        return _print_cli_action(options)

    try:
        filter_items = [FilterItem(item) for item in options.filters]
    except ArgsError as error:
        if options.json_output:
            print(
                json.dumps(
                    _json_error_document(
                        category="usage",
                        exception=snapshot_exception(
                            type(error), error, error.__traceback__
                        ),
                        exit_code=2,
                        message=str(error),
                        type_name=type(error).__name__,
                    )
                )
            )
        else:
            print_error(str(error))
        return 2

    project_root = discover_project_root()
    benchmark_baseline: BenchmarkBaseline | None = None
    if options.benchmark_baseline is not None:
        try:
            benchmark_baseline = await asyncio.to_thread(
                BenchmarkBaseline.load,
                Path(options.benchmark_baseline),
                project_root=project_root,
            )
        except BadRequestError as error:
            return _baseline_cli_error(error, json_output=options.json_output)

    runner = run_tests_programmatic_fn or run_tests_programmatic
    deferred_reporter: DeferredRunReporter | None = None
    if options.json_output:
        reporter: RunReporter = NullRunReporter(retain_passed_output=True)
    elif (
        options.update_benchmark_baseline is not None
        or options.benchmark_baseline is not None
    ):
        deferred_reporter = DeferredRunReporter(ConsoleRunReporter())
        reporter = deferred_reporter
    else:
        reporter = ConsoleRunReporter(
            retain_passed_output=options.junit_output is not None
        )
    descriptor_capture = DescriptorOutputCapture() if options.json_output else None
    capture_context = descriptor_capture or nullcontext()
    structured_error: dict[str, object] | None = None
    structured_exit_code = 2
    summary: RunResult | None = None
    with capture_context:
        try:
            summary = cast(
                "RunResult",
                await runner(
                    filter_items,
                    allow_empty=options.allow_empty,
                    capture_output=options.capture_output,
                    pdb_on_failure=options.pdb_on_failure,
                    mark=options.mark,
                    timeout=options.timeout,
                    reporter=reporter,
                    benchmark_baseline=benchmark_baseline,
                    workers=options.workers,
                ),
            )
        except asyncio.CancelledError as error:
            if not options.json_output:
                return 2
            structured_error = _json_error_document(
                category="interrupted",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
        except CollectionError as error:
            if not options.json_output:
                raise
            structured_error = _json_error_document(
                category="collection",
                exception=(
                    error.collection_diagnostic
                    or snapshot_exception(type(error), error, error.__traceback__)
                ),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
            structured_error["collection_output"] = error.collection_output
            structured_error["collection_warnings"] = list(error.collection_warnings)
        except BadRequestError as error:
            if not options.json_output:
                raise
            structured_error = _json_error_document(
                category="configuration",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
        except RunInfrastructureError as error:
            if not options.json_output:
                raise
            structured_error = _json_error_document(
                category="infrastructure",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
        except UnreachableError as error:
            if not options.json_output:
                raise
            structured_error = _json_error_document(
                category="internal",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
        except SystemExit as error:
            if not options.json_output:
                raise
            structured_exit_code = error.code if isinstance(error.code, int) else 2
            structured_error = _json_error_document(
                category="interrupted",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=structured_exit_code,
                message=str(error),
                type_name=type(error).__name__,
            )
        except KeyboardInterrupt as error:
            if not options.json_output:
                raise
            structured_error = _json_error_document(
                category="interrupted",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=2,
                message=str(error),
                type_name=type(error).__name__,
            )
        except Exception as error:
            if not options.json_output:
                raise
            structured_exit_code = 1
            structured_error = _json_error_document(
                category="unexpected",
                exception=snapshot_exception(type(error), error, error.__traceback__),
                exit_code=structured_exit_code,
                message=str(error),
                type_name=type(error).__name__,
            )
    uncaptured_output = descriptor_capture.release() if descriptor_capture else ""
    if structured_error is not None:
        structured_error["uncaptured_output"] = uncaptured_output
        print(json.dumps(structured_error))
        return structured_exit_code
    if summary is None:
        message = "test runner returned neither a run nor an error"
        raise UnreachableError(message)

    if benchmark_baseline is not None:
        summary.benchmark_baseline = BenchmarkBaselineRun(
            machine=benchmark_baseline.machine,
            mode="compare",
            path=options.benchmark_baseline or "",
        )
    elif options.update_benchmark_baseline is not None:
        baseline_run = BenchmarkBaselineRun(
            mode="update",
            path=options.update_benchmark_baseline,
        )
        if exit_code_from_summary(summary) == 0:
            try:
                baseline_update = await asyncio.to_thread(
                    BenchmarkBaseline.update,
                    Path(options.update_benchmark_baseline),
                    project_root=project_root,
                    test_results=summary.test_results,
                    filter_items=filter_items,
                    mark=options.mark,
                )
            except BadRequestError as error:
                return _baseline_cli_error(error, json_output=options.json_output)
            baseline_run = BenchmarkBaselineRun(
                machine=baseline_update.baseline.machine,
                mode="update",
                path=options.update_benchmark_baseline,
                updated_entries=baseline_update.updated_entries,
                written=True,
            )
            if not options.json_output:
                print(
                    f"Updated {baseline_update.updated_entries} benchmark baseline "
                    f"entries in {options.update_benchmark_baseline}"
                )
        summary.benchmark_baseline = baseline_run

    if options.junit_output is not None:
        try:
            _ = await asyncio.to_thread(
                Path(options.junit_output).write_text,
                build_junit_xml(summary),
                encoding="utf-8",
            )
        except OSError as error:
            message = f"Could not write JUnit output `{options.junit_output}`: {error}"
            raise BadRequestError(message) from error

    if deferred_reporter is not None:
        deferred_reporter.finish()

    if options.json_output:
        print(
            json.dumps(
                build_json_summary(
                    summary,
                    uncaptured_output=uncaptured_output,
                )
            )
        )

    return exit_code_from_summary(summary)


def main() -> None:
    """Main entry point for the CLI."""
    async_runner = cast("Callable[[Coroutine[object, object, int]], int]", asyncio.run)
    sys.exit(main_inner(async_runner=async_runner))


def main_inner(  # noqa: C901, PLR0911, PLR0912
    *,
    async_runner: Callable[[Coroutine[object, object, int]], int],
    argv: list[str] | None = None,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    json_requested = "--json-output" in arguments
    coroutine = run_script(arguments)

    def render_error(
        error: BaseException,
        *,
        category: str,
        exit_code: int,
    ) -> int:
        if json_requested:
            diagnostic = (
                error.collection_diagnostic
                if isinstance(error, CollectionError)
                else None
            ) or snapshot_exception(type(error), error, error.__traceback__)
            document = _json_error_document(
                category=category,
                exception=diagnostic,
                exit_code=exit_code,
                message=str(error),
                type_name=type(error).__name__,
            )
            if isinstance(error, CollectionError):
                document["collection_output"] = error.collection_output
                document["collection_warnings"] = list(error.collection_warnings)
            print(json.dumps(document))
        return exit_code

    try:
        return async_runner(coroutine)
    except CollectionError as error:
        if json_requested:
            return render_error(error, category="collection", exit_code=2)
        if error.__cause__ is None:
            print_error(f"Collection error: {error}")
        else:
            formatted = "".join(traceback.format_exception(error)).rstrip()
            print_error(f"Collection error:\n{formatted}")
        return 2
    except BadRequestError as error:
        if json_requested:
            return render_error(error, category="configuration", exit_code=2)
        print_error(f"Bad request error: {error}")
        return 2
    except RunInfrastructureError as error:
        if json_requested:
            return render_error(error, category="infrastructure", exit_code=2)
        print_error(f"Run infrastructure error: {error}")
        return 2
    except UnreachableError as error:
        if json_requested:
            return render_error(error, category="internal", exit_code=2)
        print_error(f"Internal error: {error}")
        return 2
    except KeyboardInterrupt as error:
        if json_requested:
            return render_error(error, category="interrupted", exit_code=2)
        print_error("Interrupted by user")
        return 2
    except Exception as error:
        if json_requested:
            return render_error(error, category="unexpected", exit_code=1)
        print_error(f"Unexpected error: {error}")
        return 1
    finally:
        coroutine.close()


if __name__ == "__main__":
    main()
