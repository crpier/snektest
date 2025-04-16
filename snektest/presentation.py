import sys
from shutil import get_terminal_size
from typing import Any, Mapping

from colorama import Fore


class ColoredString(str):
    def __new__(cls, string: str, __original_length__: int):
        return super().__new__(cls, string)

    def __init__(self, __string__: str, original_length: int):
        self.original_length = original_length
        super().__init__()


IS_TTY = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# Color definitions using colorama
class Colors:
    RESET = Fore.RESET if IS_TTY else ""
    RED = Fore.RED if IS_TTY else ""
    GREEN = Fore.GREEN if IS_TTY else ""
    YELLOW = Fore.YELLOW if IS_TTY else ""
    BLUE = Fore.BLUE if IS_TTY else ""
    BLUE = Fore.BLUE if IS_TTY else ""

    @classmethod
    def get_colors(cls) -> list[str]:
        return [
            getattr(cls, attr)
            for attr in dir(cls)
            if not attr.startswith("__") and not callable(getattr(cls, attr))
        ]

    @classmethod
    def remove_color_codes(cls, text: str) -> str:
        color_codes = [color for color in cls.get_colors()]
        for code in color_codes:
            text = text.replace(code, "")
        return text

    @classmethod
    def apply_color(cls, text: str, color: str) -> str:
        return f"{color}{text}{cls.RESET}"

    @classmethod
    def apply_multiple_colors(
        cls, color_map: Mapping[str, str | None]
    ) -> ColoredString:
        result_list = []
        original_length = 0
        for substring, color in color_map.items():
            colored_substring = (
                cls.apply_color(substring, color) if color else substring
            )
            result_list.append(colored_substring)
            original_length += len(substring)
        result = ColoredString("".join(result_list), original_length)
        return result


class Output:
    def __init__(self, verbose: bool):
        self.verbose = verbose

    def print_test_output(
        self,
        test_name: str,
        test_params: tuple[Any],
        test_status: str,
        fixtures: dict[str, str],
    ) -> None:
        if self.verbose:
            test_status_str = Colors.apply_color(test_status, Colors.GREEN)
            if len(fixtures) == 0:
                fixtures_str = ""
            else:
                fixtures_str = " with: " + ", ".join(
                    [f"{name}={value}" for name, value in fixtures.items()]
                )
            if test_params == ():
                test_params_str = ""
            else:
                test_params_str = f" on {test_params}"
            print(f"{test_name}{test_params_str}{fixtures_str} {test_status_str}")


def pad_string_to_screen_width(summary: ColoredString, pad_char: str = "-") -> str:
    # TODO: how would I go about validating that the pad_char is a single character?
    terminal_width, _ = get_terminal_size()

    # Add padding if there's enough room
    if summary.original_length + 2 < terminal_width:  # 2 spaces added extra
        padding = pad_char * ((terminal_width - summary.original_length - 2) // 2)
        result = f"{padding} {summary} {padding}"
    else:
        result = summary
    return result
