import asyncio
import json
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from snektest.agent_docs import (
    get_agent_docs,
    get_example_source,
    get_examples_listing,
)
from snektest.benchmark_baseline import (
    BenchmarkBaseline,
    MachineFingerprint,
    discover_project_root,
)
from snektest.collection import TestsQueue, load_tests_from_filters
from snektest.execution import run_tests
from snektest.models import (
    ArgsError,
    BadRequestError,
    CollectionError,
    ErrorResult,
    FailedResult,
    FilterItem,
    PassedResult,
    TeardownFailure,
    TestResult,
    UnreachableError,
)
from snektest.presenter import print_error
from snektest.reporting import (
    ConsoleRunReporter,
    DeferredRunReporter,
    NullRunReporter,
    RunReporter,
)


def _json_result_status(result: TestResult) -> str:
    match result.result:
        case PassedResult():
            return "passed"
        case FailedResult():
            return "failed"
        case ErrorResult():
            return "error"


def _json_exception(
    exc_type: type[BaseException], exc_value: BaseException
) -> dict[str, str]:
    return {"type": exc_type.__name__, "message": str(exc_value)}


def _baseline_cli_error(error: BadRequestError, *, json_output: bool) -> int:
    """Keep baseline configuration errors machine-readable in JSON mode."""
    if json_output:
        print(
            json.dumps(
                {
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                }
            )
        )
        return 2
    raise error


def _json_test_entry(result: TestResult) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": str(result.name),
        "duration": result.duration,
        "markers": list(result.markers),
        "status": _json_result_status(result),
    }
    match result.result:
        case FailedResult(exc_type=exc_type, exc_value=exc_value):
            entry["exception"] = _json_exception(exc_type, exc_value)
        case ErrorResult(exc_type=exc_type, exc_value=exc_value):
            entry["exception"] = _json_exception(exc_type, exc_value)
        case PassedResult(measurements=measurements):
            if measurements:
                entry["memory_measurements"] = [
                    {
                        "peak_bytes": measurement.peak_bytes,
                        "growth_slope": measurement.growth_slope,
                        "rounds": measurement.rounds,
                        "peak_budget": measurement.peak_budget,
                        "slope_budget": measurement.slope_budget,
                    }
                    for measurement in measurements
                ]
    benchmarks = result.result.benchmarks
    comparisons_by_index = {
        comparison.measurement_index: comparison
        for comparison in result.result.benchmark_comparisons
    }
    if benchmarks:
        benchmark_entries: list[dict[str, object]] = []
        for index, benchmark in enumerate(benchmarks):
            benchmark_entry: dict[str, object] = {
                "name": benchmark.name,
                "rounds": benchmark.rounds,
                "warmup": benchmark.warmup,
                "disable_gc": benchmark.disable_gc,
                "min_seconds": benchmark.min_seconds,
                "median_seconds": benchmark.median_seconds,
                "p95_seconds": benchmark.p95_seconds,
                "mean_seconds": benchmark.mean_seconds,
                "stddev_seconds": benchmark.stddev_seconds,
                "median_budget_seconds": benchmark.median_budget_seconds,
                "p95_budget_seconds": benchmark.p95_budget_seconds,
                "median_regression_below": benchmark.median_regression_below,
                "regression_noise_floor_seconds": benchmark.regression_noise_floor_seconds,
            }
            comparison = comparisons_by_index.get(index)
            if comparison is not None:
                benchmark_entry["baseline_comparison"] = {
                    "verdict": comparison.verdict,
                    "baseline_median_seconds": comparison.baseline_median_seconds,
                    "observed_median_seconds": comparison.observed_median_seconds,
                    "change_ratio": comparison.change_ratio,
                    "regression_below": comparison.regression_below,
                    "noise_floor_seconds": comparison.noise_floor_seconds,
                    "allowed_increase_seconds": comparison.allowed_increase_seconds,
                    "limit_seconds": comparison.limit_seconds,
                }
            benchmark_entries.append(benchmark_entry)
        entry["benchmark_measurements"] = benchmark_entries
    if result.fixture_teardown_failures:
        entry["fixture_teardown_failures"] = [
            {
                "fixture_name": failure.fixture_name,
                "exception": _json_exception(failure.exc_type, failure.exc_value),
            }
            for failure in result.fixture_teardown_failures
        ]
    return entry


def build_json_summary(summary: TestRunSummary) -> dict[str, object]:
    output: dict[str, object] = {
        "passed": summary.passed,
        "failed": summary.failed,
        "errors": summary.errors,
        "fixture_teardown_failed": summary.fixture_teardown_failed,
        "session_teardown_failed": summary.session_teardown_failed,
        "session_teardown_failures": [
            {
                "fixture_name": failure.fixture_name,
                "exception": _json_exception(failure.exc_type, failure.exc_value),
            }
            for failure in summary.session_teardown_failures
        ],
        "tests": [_json_test_entry(result) for result in summary.test_results],
    }
    baseline = getattr(summary, "benchmark_baseline", None)
    if isinstance(baseline, BenchmarkBaselineRun):
        machine_output: dict[str, object] | None = None
        if baseline.machine is not None:
            machine_output = {
                "architecture": baseline.machine.architecture,
                "logical_cpu_count": baseline.machine.logical_cpu_count,
                "processor": baseline.machine.processor,
                "python_implementation": baseline.machine.python_implementation,
                "python_version": baseline.machine.python_version,
                "system": baseline.machine.system,
            }
        output["benchmark_baseline"] = {
            "mode": baseline.mode,
            "path": baseline.path,
            "machine": machine_output,
            "written": baseline.written,
            "updated_entries": baseline.updated_entries,
        }
    return output


@dataclass(frozen=True)
class BenchmarkBaselineRun:
    """Machine-readable metadata for one compare or update CLI mode."""

    mode: Literal["compare", "update"]
    path: str
    machine: MachineFingerprint | None = None
    updated_entries: int = 0
    written: bool = False


@dataclass
class TestRunSummary:
    """Summary of test run results."""

    total_tests: int
    passed: int
    failed: int
    errors: int
    fixture_teardown_failed: int
    session_teardown_failed: int
    test_results: list[TestResult]
    session_teardown_failures: list[TeardownFailure]
    benchmark_baseline: BenchmarkBaselineRun | None = None


type CliAction = Literal["agent_docs", "help", "list_examples", "show_example"]

_DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CliOptions:
    action: CliAction | None = None
    benchmark_baseline: str | None = None
    capture_output: bool = True
    example_name: str | None = None
    filters: tuple[str, ...] = ()
    json_output: bool = False
    pdb_on_failure: bool = False
    mark: str | None = None
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS
    update_benchmark_baseline: str | None = None


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
  -s                Disable stdout/stderr capture
  --agent-docs      Print AI-agent usage guide
  --llms            Alias for --agent-docs
  --examples        List bundled examples
  --example NAME    Print a bundled example
  --json-output     Print machine-readable JSON summary
  --benchmark-baseline PATH
                    Compare opted-in benchmarks with a machine-bound baseline
  --update-benchmark-baseline PATH
                    Atomically update opted-in benchmarks after a passing run
  --mark MARK       Run tests marked fast, medium, or slow; marking tests is recommended
  --timeout SECONDS Override the 60-second async-test timeout
  --no-timeout      Disable the default async-test timeout
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
    """Parse `--timeout` and its value, rejecting repeats and non-positive numbers.

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
    if timeout <= 0:
        return ParseError(f"Invalid --timeout value: `{raw_value}`. Must be positive.")
    return timeout, value_index


def _print_cli_action(options: CliOptions) -> int:
    output = ""
    if options.action == "help":
        output = HELP_TEXT
    elif options.action == "agent_docs":
        output = get_agent_docs()
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


def parse_cli_args(argv: list[str]) -> CliOptions | ParseError:  # noqa: C901, PLR0911, PLR0912, PLR0915
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
    benchmark_baseline: str | None = None
    capture_output = True
    example_name: str | None = None
    json_output = False
    mark: str | None = None
    pdb_on_failure = False
    timeout: float | None = _DEFAULT_TIMEOUT_SECONDS
    timeout_option_seen = False
    update_benchmark_baseline: str | None = None
    filters: list[str] = []
    duplicate_action = ParseError("Only one help/docs/examples command is supported")

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
        elif arg.startswith("-"):
            return ParseError(f"Invalid option: `{arg}`")
        else:
            filters.append(arg)
        index += 1

    if action is not None and (
        filters
        or benchmark_baseline is not None
        or update_benchmark_baseline is not None
    ):
        return ParseError(
            "Cannot combine help/docs/examples commands with test filters or baseline options"
        )
    if action is None and not filters:
        filters.append(".")

    return CliOptions(
        action=action,
        benchmark_baseline=benchmark_baseline,
        capture_output=capture_output,
        example_name=example_name,
        filters=tuple(filters),
        json_output=json_output,
        mark=mark,
        pdb_on_failure=pdb_on_failure,
        timeout=timeout,
        update_benchmark_baseline=update_benchmark_baseline,
    )


async def _run_tests_with_producer_thread(  # noqa: PLR0913
    filter_items: list[FilterItem],
    *,
    capture_output: bool,
    pdb_on_failure: bool,
    mark: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109
    reporter: RunReporter | None = None,
    benchmark_baseline: BenchmarkBaseline | None = None,
) -> tuple[list[TestResult], list[TeardownFailure]]:
    queue = TestsQueue()
    collection_exception: list[BaseException] = []

    producer_thread = threading.Thread(
        target=load_tests_from_filters,
        kwargs={
            "filter_items": filter_items,
            "queue": queue,
            "loop": asyncio.get_running_loop(),
            "mark": mark,
            "exception_holder": collection_exception,
        },
    )

    producer_thread.start()

    try:
        test_results, session_teardown_failures = await run_tests(
            queue=queue,
            capture_output=capture_output,
            pdb_on_failure=pdb_on_failure,
            timeout=timeout,
            collection_failed=lambda: bool(collection_exception),
            reporter=reporter,
            benchmark_baseline=benchmark_baseline,
        )
    finally:
        producer_thread.join()
        if collection_exception:
            raise collection_exception[0]

    return test_results, session_teardown_failures


def exit_code_from_summary(summary: TestRunSummary) -> int:
    has_failures = (
        summary.failed > 0
        or summary.errors > 0
        or summary.fixture_teardown_failed > 0
        or summary.session_teardown_failed > 0
    )
    return 1 if has_failures else 0


async def run_tests_programmatic(  # noqa: PLR0913
    filter_items: list[FilterItem],
    *,
    capture_output: bool = True,
    pdb_on_failure: bool = False,
    mark: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109
    reporter: RunReporter | None = None,
    benchmark_baseline: BenchmarkBaseline | None = None,
) -> TestRunSummary:
    """Run tests and return structured results instead of printing.

    This is the programmatic API for testing snektest itself.
    Returns structured data instead of printing by default.

    Args:
        filter_items: List of filter items to run tests from
        capture_output: Whether to capture test output
        reporter: Optional progress reporter. Defaults to no presentation side effects.

    Returns:
        TestRunSummary with test results and counts
    """
    if mark is not None and not _is_valid_mark_value(mark):
        raise BadRequestError(_invalid_mark_message(mark))

    test_results, session_teardown_failures = await _run_tests_with_producer_thread(
        filter_items,
        capture_output=capture_output,
        pdb_on_failure=pdb_on_failure,
        mark=mark,
        timeout=timeout,
        reporter=reporter or NullRunReporter(),
        benchmark_baseline=benchmark_baseline,
    )

    return TestRunSummary(
        total_tests=len(test_results),
        passed=sum(1 for r in test_results if isinstance(r.result, PassedResult)),
        failed=sum(1 for r in test_results if isinstance(r.result, FailedResult)),
        errors=sum(1 for r in test_results if isinstance(r.result, ErrorResult)),
        fixture_teardown_failed=sum(
            1 for r in test_results if r.fixture_teardown_failures
        ),
        session_teardown_failed=len(session_teardown_failures),
        test_results=test_results,
        session_teardown_failures=session_teardown_failures,
    )


async def run_script(  # noqa: C901, PLR0911, PLR0912
    argv: list[str] | None = None,
    *,
    run_tests_programmatic_fn: Callable[..., Coroutine[object, object, object]]
    | None = None,
) -> int:
    """Parse arguments and run tests."""
    parsed = parse_cli_args(sys.argv[1:] if argv is None else argv)
    if isinstance(parsed, ParseError):
        print_error(parsed.message)
        return 2

    options = parsed

    if options.action is not None:
        return _print_cli_action(options)

    try:
        filter_items = [FilterItem(item) for item in options.filters]
    except ArgsError as e:
        print_error(str(e))
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
        reporter: RunReporter = NullRunReporter()
    elif (
        options.update_benchmark_baseline is not None
        or options.benchmark_baseline is not None
    ):
        deferred_reporter = DeferredRunReporter(ConsoleRunReporter())
        reporter = deferred_reporter
    else:
        reporter = ConsoleRunReporter()
    try:
        summary = cast(
            "TestRunSummary",
            await runner(
                filter_items,
                capture_output=options.capture_output,
                pdb_on_failure=options.pdb_on_failure,
                mark=options.mark,
                timeout=options.timeout,
                reporter=reporter,
                benchmark_baseline=benchmark_baseline,
            ),
        )
    except asyncio.CancelledError:
        return 2
    except BadRequestError as error:
        return _baseline_cli_error(error, json_output=options.json_output)

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

    if deferred_reporter is not None:
        deferred_reporter.finish()

    if options.json_output:
        print(json.dumps(build_json_summary(summary)))

    return exit_code_from_summary(summary)


def main() -> None:
    """Main entry point for the CLI."""
    async_runner = cast("Callable[[Coroutine[object, object, int]], int]", asyncio.run)
    sys.exit(main_inner(async_runner=async_runner))


def main_inner(
    *,
    async_runner: Callable[[Coroutine[object, object, int]], int],
    argv: list[str] | None = None,
) -> int:
    coroutine = run_script(argv)
    try:
        return async_runner(coroutine)
    except CollectionError as e:
        if e.__cause__ is None:
            print_error(f"Collection error: {e}")
        else:
            formatted = "".join(traceback.format_exception(e)).rstrip()
            print_error(f"Collection error:\n{formatted}")
        return 2
    except BadRequestError as e:
        print_error(f"Bad request error: {e}")
        return 2
    except UnreachableError as e:
        print_error(f"Internal error: {e}")
        return 2
    except KeyboardInterrupt:
        print_error("Interrupted by user")
        return 2
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return 1
    finally:
        coroutine.close()


if __name__ == "__main__":
    main()
