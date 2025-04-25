from dataclasses import dataclass
from enum import StrEnum, auto

# TODO: I think presentation should import results, not the other way around
from snektest.presentation import Colors, pad_string_to_screen_width


class TestStatus(StrEnum):
    passed = auto()
    failed = auto()
    xfailed = auto()
    xpassed = auto()
    skipped_unconditionally = auto()
    skipped_conditionally = auto()
    skippped_dynamically = auto()


@dataclass
class TestResult:
    status: TestStatus
    message: str


def show_results(test_results: dict[str, TestResult]) -> None:
    no_passed = sum(
        1 for test in test_results.values() if test.status == TestStatus.passed
    )
    no_failed = sum(
        1 for test in test_results.values() if test.status == TestStatus.failed
    )
    no_xfailed = sum(
        1 for test in test_results.values() if test.status == TestStatus.xfailed
    )
    no_xpassed = sum(
        1 for test in test_results.values() if test.status == TestStatus.xpassed
    )

    message = ""

    for test_name, test_result in test_results.items():
        if test_result.status == TestStatus.failed:
            message += (
                f"{Colors.RED}{test_name}{Colors.RESET}:\n{test_result.message}\n"
            )
        if test_result.status == TestStatus.xfailed:
            message += f"{Colors.YELLOW}{test_name}: {test_result.message}\n"

    colored_message = {
        f"{no_passed} passed, ": Colors.GREEN,
        f"{no_failed} failed, ": Colors.RED,
        f"{no_xfailed} xfailed, ": Colors.YELLOW,
        f"{no_xpassed} xpassed, ": Colors.BLUE,
        f"{len(test_results)} total": None,
    }
    summary = Colors.apply_multiple_colors(colored_message)

    summary = pad_string_to_screen_width(summary)
