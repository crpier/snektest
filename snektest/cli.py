import asyncio
import json
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
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


def _json_result_status(result: TestResult) -> str:
    match result.result:
        case PassedResult():
            return "passed"
        case FailedResult():
            return "failed"
        case ErrorResult():
            return "error"


def build_json_summary(summary: TestRunSummary) -> dict[str, object]:
    return {
        "passed": summary.passed,
        "failed": summary.failed,
        "errors": summary.errors,
        "fixture_teardown_failed": summary.fixture_teardown_failed,
        "session_teardown_failed": summary.session_teardown_failed,
        "tests": [
            {
                "name": str(result.name),
                "duration": result.duration,
                "markers": list(result.markers),
                "status": _json_result_status(result),
            }
            for result in summary.test_results
        ],
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
    json_output: bool = False
    pdb_on_failure: bool = False
    mark: str | None = None


@dataclass
class CliParseState:
    action: CliAction | None = None
    capture_output: bool = True
    example_name: str | None = None
    json_output: bool = False
    mark: str | None = None
    pdb_on_failure: bool = False
    potential_filter: list[str] = field(default_factory=list[str])


PARSE_ERROR = -1
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


def _parse_mark_value(
    argv: list[str], index: int, mark: str | None
) -> tuple[str, int] | None:
    if mark is not None:
        print_error("Only one --mark value is supported")
        return None
    if index + 1 >= len(argv):
        print_error("Missing value for --mark")
        return None
    mark_value = argv[index + 1]
    if mark_value.startswith("-"):
        print_error("Missing value for --mark")
        return None
    if not _is_valid_mark_value(mark_value):
        print_error(_invalid_mark_message(mark_value))
        return None
    return mark_value, index + 1


def _parse_example_value(argv: list[str], index: int) -> tuple[str, int] | None:
    if index + 1 >= len(argv):
        print_error("Missing value for --example")
        return None
    example_name = argv[index + 1]
    if example_name.startswith("-"):
        print_error("Missing value for --example")
        return None
    return example_name, index + 1


def _set_cli_action(state: CliParseState, new_action: CliAction) -> bool:
    if state.action is not None:
        print_error("Only one help/docs/examples command is supported")
        return False
    state.action = new_action
    return True


def _parse_example_action(argv: list[str], index: int, state: CliParseState) -> int:
    next_index = PARSE_ERROR
    if _set_cli_action(state, "show_example"):
        parsed_example = _parse_example_value(argv, index)
        if parsed_example is not None:
            state.example_name, next_index = parsed_example
    return next_index


def _parse_action_option(command: str, state: CliParseState) -> int:
    actions: dict[str, CliAction] = {
        "--agent-docs": "agent_docs",
        "--examples": "list_examples",
        "--help": "help",
        "--llms": "agent_docs",
        "-h": "help",
    }
    next_index = PARSE_ERROR
    action = actions.get(command)
    if action is not None and _set_cli_action(state, action):
        next_index = 0
    return next_index


def _parse_option(argv: list[str], index: int, state: CliParseState) -> int:
    command = argv[index]
    next_index = index
    if command in {"-h", "--agent-docs", "--examples", "--help", "--llms"}:
        parsed_index = _parse_action_option(command, state)
        next_index = index if parsed_index != PARSE_ERROR else PARSE_ERROR
    elif command == "--example":
        next_index = _parse_example_action(argv, index, state)
    elif command == "-s":
        state.capture_output = False
    elif command == "--json-output":
        state.json_output = True
    elif command == "--pdb":
        state.pdb_on_failure = True
    elif command == "--mark":
        parsed_mark = _parse_mark_value(argv, index, state.mark)
        if parsed_mark is None:
            next_index = PARSE_ERROR
        else:
            state.mark, next_index = parsed_mark
    else:
        print_error(f"Invalid option: `{command}`")
        next_index = PARSE_ERROR
    return next_index


def _parse_positional(argv: list[str], index: int, state: CliParseState) -> int:
    command = argv[index]
    next_index = index
    if command == "examples":
        if not _set_cli_action(state, "list_examples"):
            next_index = PARSE_ERROR
    elif command == "example":
        next_index = _parse_example_action(argv, index, state)
    else:
        state.potential_filter.append(command)
    return next_index


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


def _finish_parse_state(state: CliParseState) -> tuple[list[str], CliOptions] | int:
    potential_filter = state.potential_filter
    if state.action is not None and potential_filter:
        print_error("Cannot combine help/docs/examples commands with test filters")
        return 2

    if state.action is None and not potential_filter:
        potential_filter.append(".")

    options = CliOptions(
        action=state.action,
        capture_output=state.capture_output,
        example_name=state.example_name,
        json_output=state.json_output,
        pdb_on_failure=state.pdb_on_failure,
        mark=state.mark,
    )
    return potential_filter, options


def parse_cli_args(argv: list[str]) -> tuple[list[str], CliOptions] | int:
    state = CliParseState()

    index = 0
    while index < len(argv):
        command = argv[index]
        if command.startswith("-"):
            index = _parse_option(argv, index, state)
        else:
            index = _parse_positional(argv, index, state)
        if index == PARSE_ERROR:
            return 2
        index += 1

    return _finish_parse_state(state)


async def _run_tests_with_producer_thread(
    filter_items: list[FilterItem],
    *,
    capture_output: bool,
    pdb_on_failure: bool,
    mark: str | None = None,
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
            collection_failed=lambda: bool(collection_exception),
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


async def run_tests_programmatic(
    filter_items: list[FilterItem],
    *,
    capture_output: bool = True,
    pdb_on_failure: bool = False,
    mark: str | None = None,
) -> TestRunSummary:
    """Run tests and return structured results instead of printing.

    This is the programmatic API for testing snektest itself.
    Returns structured data instead of just printing and exiting.

    Args:
        filter_items: List of filter items to run tests from
        capture_output: Whether to capture test output

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
    if isinstance(parsed, int):
        return parsed

    potential_filter, options = parsed

    if options.action is not None:
        return _print_cli_action(options)

    try:
        filter_items = [FilterItem(item) for item in potential_filter]
    except ArgsError as e:
        print_error(str(e))
        return 2

    runner = run_tests_programmatic_fn or run_tests_programmatic
    try:
        summary = cast(
            "TestRunSummary",
            await runner(
                filter_items,
                capture_output=options.capture_output,
                pdb_on_failure=options.pdb_on_failure,
                mark=options.mark,
            ),
        )
    except asyncio.CancelledError:
        return 2

    if options.json_output:
        print(json.dumps(build_json_summary(summary)))

    return exit_code_from_summary(summary)


def main() -> None:
    """Main entry point for the CLI."""
    async_runner = cast("Callable[[object], int]", asyncio.run)
    sys.exit(main_inner(async_runner=async_runner))


def main_inner(
    *,
    async_runner: Callable[[object], int],
    argv: list[str] | None = None,
) -> int:
    try:
        coroutine = run_script(argv)
        return async_runner(coroutine)
    except CollectionError as e:
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


if __name__ == "__main__":
    main()
