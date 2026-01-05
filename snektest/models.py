from dataclasses import dataclass
from enum import Enum, auto
from io import StringIO
from itertools import product
from pathlib import Path
from types import TracebackType
from typing import Any


class CollectionError(BaseException): ...


class ArgsError(BaseException): ...


class UnreachableError(BaseException): ...


class BadRequestError(BaseException):
    """When user didn't write test code correctly"""


class AssertionFailure(AssertionError):  # noqa: N818
    def __init__(
        self,
        message: str,
        *,
        actual: Any = None,
        expected: Any = None,
        operator: str | None = None,
    ) -> None:
        super().__init__(message)
        self.actual = actual
        self.expected = expected
        self.operator = operator


SnektestError = CollectionError | ArgsError | UnreachableError | AssertionFailure


class FilterItem:
    def __init__(self, raw_input: str) -> None:
        if "::" not in raw_input:
            path = Path(raw_input)
            function_name = None
            params = None
        else:
            file_part, rest = raw_input.split("::", 1)
            if rest == "":
                msg = f"Invalid test filter - nothing given after semicolon in '{raw_input}'"
                raise ArgsError(msg)

            path = Path(file_part)

            if "[" in rest:
                if not rest.endswith("]"):
                    msg = f"Invalid test filter - unterminated `[` in '{raw_input}'"
                    raise ArgsError(msg)
                rest = rest.removesuffix("]")
                function_name, params = rest.split("[", 1)
            else:
                function_name = rest
                params = None

        if not path.exists():
            msg = f"Invalid test filter - provided path does not exist in '{raw_input}'"
            raise ArgsError(msg)

        if path.is_file() and path.suffix != ".py":
            msg = f"Invalid test filter - file is not a Python script in '{raw_input}'"
            raise ArgsError(msg)

        if path.is_file() and not path.name.startswith("test_"):
            msg = (
                f"Invalid test filter - file does not start with _test in '{raw_input}'"
            )
            raise ArgsError(msg)

        if function_name is not None and not function_name.isidentifier():
            msg = f"Invalid test filter - invalid identifier {function_name} in '{raw_input}'"
            raise ArgsError(msg)

        self.file_path = path
        self.function_name = function_name
        self.params = params

    def __str__(self) -> str:
        result = str(self.file_path)
        if self.function_name is not None:
            result += f"::{self.function_name}"
        if self.params:
            result += f"[{self.params}]"
        return result

    def __repr__(self) -> str:
        return f"FilterItem(file_path={self.file_path!r}, function_name={self.function_name!r}, params={self.params!r})"


# Set kw_only so we can write attributes in the order they appear
@dataclass(kw_only=True)
class TestName:
    file_path: Path
    func_name: str
    params_part: str

    def __str__(self) -> str:
        result = str(self.file_path)
        result += f"::{self.func_name}"
        if self.params_part:
            result += f"[{self.params_part}]"
        return result


class PassedResult: ...


@dataclass
class Param[T]:
    value: T
    name: str

    @staticmethod
    def to_dict(
        params: tuple[list[Param[Any]], ...],
    ) -> dict[str, tuple[Param[Any], ...]]:
        """Create a dictionary that contains all possible params combinations.

        For tests with no parameters, returns {"": ()} to ensure the test runs once.
        """
        # Handle the no-params case explicitly
        if not params:
            return {"": ()}

        combinations = product(*params)
        result: dict[str, tuple[Param[Any], ...]] = {}
        for combination in combinations:
            result[", ".join([param.name for param in combination])] = combination
        return result


class Scope(Enum):
    FUNCTION = auto()
    SESSION = auto()


@dataclass(frozen=True)
class FailedResult:
    exc_type: type[BaseException]
    exc_value: BaseException
    traceback: TracebackType


@dataclass(frozen=True)
class ErrorResult:
    exc_type: type[BaseException]
    exc_value: BaseException
    traceback: TracebackType


@dataclass(frozen=True)
class TeardownFailure:
    """Represents a fixture teardown failure"""

    fixture_name: str
    exc_type: type[BaseException]
    exc_value: BaseException
    traceback: TracebackType


@dataclass
class TestResult:
    name: TestName
    duration: float
    result: PassedResult | FailedResult | ErrorResult
    captured_output: StringIO
    fixture_teardown_failures: list[TeardownFailure]
    fixture_teardown_output: str | None
    warnings: list[str]
