import asyncio
import json
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Literal, cast

from snektest.agent_docs import (
    get_agent_docs,
    get_example_source,
    get_examples_listing,
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
from snektest.reporting import ConsoleRunReporter, NullRunReporter, RunReporter


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
        case PassedResult():
            pass
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
    return {
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


type CliAction = Literal["agent_docs", "help", "list_examples", "show_example"]


@dataclass(frozen=True)
class CliOptions:
    action: CliAction | None = None
    capture_output: bool = True
    example_name: str | None = None
    filters: tuple[str, ...] = ()
    json_output: bool = False
    pdb_on_failure: bool = False
    mark: str | None = None
    timeout: float | None = None


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
  --mark MARK       Run tests marked fast, medium, or slow; marking tests is recommended
  --timeout SECONDS Fail any async test that runs longer than SECONDS (async-only)
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
    argv: list[str], index: int, current_timeout: float | None
) -> tuple[float, int] | ParseError:
    """Parse `--timeout` and its value, rejecting repeats and non-positive numbers.

    Returns the timeout in seconds with the index its value was consumed from, or
    a ParseError.
    """
    if current_timeout is not None:
        return ParseError("Only one --timeout value is supported")
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


def parse_cli_args(argv: list[str]) -> CliOptions | ParseError:  # noqa: C901, PLR0911, PLR0912
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
    capture_output = True
    example_name: str | None = None
    json_output = False
    mark: str | None = None
    pdb_on_failure = False
    timeout: float | None = None
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
        elif arg == "--pdb":
            pdb_on_failure = True
        elif arg == "--mark":
            parsed_mark = _parse_mark_flag(argv, index, mark)
            if isinstance(parsed_mark, ParseError):
                return parsed_mark
            mark, index = parsed_mark
        elif arg == "--timeout":
            parsed_timeout = _parse_timeout_flag(argv, index, timeout)
            if isinstance(parsed_timeout, ParseError):
                return parsed_timeout
            timeout, index = parsed_timeout
        elif arg.startswith("-"):
            return ParseError(f"Invalid option: `{arg}`")
        else:
            filters.append(arg)
        index += 1

    if action is not None and filters:
        return ParseError(
            "Cannot combine help/docs/examples commands with test filters"
        )
    if action is None and not filters:
        filters.append(".")

    return CliOptions(
        action=action,
        capture_output=capture_output,
        example_name=example_name,
        filters=tuple(filters),
        json_output=json_output,
        mark=mark,
        pdb_on_failure=pdb_on_failure,
        timeout=timeout,
    )


async def _run_tests_with_producer_thread(  # noqa: PLR0913
    filter_items: list[FilterItem],
    *,
    capture_output: bool,
    pdb_on_failure: bool,
    mark: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109
    reporter: RunReporter | None = None,
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


async def run_script(
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

    runner = run_tests_programmatic_fn or run_tests_programmatic
    reporter = NullRunReporter() if options.json_output else ConsoleRunReporter()
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
            ),
        )
    except asyncio.CancelledError:
        return 2

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
