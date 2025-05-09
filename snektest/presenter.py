from rich.console import Console
from rich.text import Text

from snektest.models import TestResult


class DisplayAdapter:
    def __init__(self) -> None:
        self.console = Console()

    def print_test_result(self, test_name: str, test_result: TestResult) -> None:
        result_string = Text(
            test_result,
            style="red" if test_result == "failed" else "green",
        )
        self.console.print(Text(test_name), "...", result_string)
