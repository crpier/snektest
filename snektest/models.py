from dataclasses import dataclass
from enum import Enum, auto
from itertools import product
from pathlib import Path
from types import TracebackType
from typing import Any, cast


class CollectionError(BaseException): ...


class ArgsError(BaseException): ...


# TODO: make sure we raise custom errors everywhere possible
SnektestError = CollectionError | ArgsError


class FilterItem:
    def __init__(self, raw_input: str) -> None:
        """
        Raises:
            ValueError: if given bad input
        """
        if "::" not in raw_input:
            path = Path(raw_input)
            function_name = None
            params = cast("tuple[str, ...]", ())
        else:
            file_part, rest = raw_input.split("::", 1)
            if rest == "":
                msg = f"Invalid test filter - nothing given after semicolon in '{raw_input}'"
                raise ValueError(msg)

            path = Path(file_part)

            if "[" in rest:
                if not rest.endswith("]"):
                    msg = f"Invalid test filter - unterminated `[` in '{raw_input}'"
                    raise ValueError(msg)
                rest = rest.removesuffix("]")
                function_name, raw_params = rest.split("[", 1)
                params = tuple(param.strip() for param in raw_params.split(","))
            else:
                function_name = rest
                params = cast("tuple[str, ...]", ())

        if not path.exists():
            msg = f"Invalid test filter - provided path does not exist in '{raw_input}'"
            raise ValueError(msg)

        if path.is_file() and path.suffix != ".py":
            msg = f"Invalid test filter - file is not a Python script in '{raw_input}'"
            raise ValueError(msg)

        if path.is_file() and not path.name.startswith("test_"):
            msg = (
                f"Invalid test filter - file does not start with _test in '{raw_input}'"
            )
            raise ValueError(msg)

        if function_name is not None and not function_name.isidentifier():
            msg = f"Invalid test filter - invalid identifier {function_name} in '{raw_input}'"
            raise ValueError(msg)

        self.file_path = path
        self.function_name = function_name
        self.params = params

    def __str__(self) -> str:
        result = str(self.file_path)
        if self.function_name is not None:
            result += f"::{self.function_name}"
        if self.params:
            result += f"[{', '.join(self.params)}]"
        return result

    def __repr__(self) -> str:
        return f"FilterItem(file_path={self.file_path!r}, function_name={self.function_name!r}, params={self.params!r})"


# Set kw_only so we can write attributes in the order they appear
# TODO: would this actually be better as a regular class?
@dataclass(kw_only=True)
class TestName:
    file_path: Path
    func_name: str
    param_names: tuple[str, ...]

    def __str__(self) -> str:
        result = str(self.file_path)
        result += f"::{self.func_name}"
        if self.param_names:
            result += f"[{', '.join(self.param_names)}]"
        return result


class PassedResult: ...


# TODO: param names need to be known at import time -> allow users to load
# their value lazily
@dataclass
class Param[T]:
    value: T
    name: str

    @staticmethod
    def to_dict(
        params: tuple[list[Param[Any]], ...],
    ) -> dict[tuple[str, ...], tuple[Param[Any], ...]]:
        """Create a dictionary that contains all possible params combinations"""
        combinations = product(*params)
        result: dict[tuple[str, ...], tuple[Param[Any], ...]] = {}
        for combination in combinations:
            result[tuple(param.name for param in combination)] = combination
        return result


class Scope(Enum):
    FUNCTION = auto()
    SESSION = auto()


@dataclass(frozen=True)
class FailedResult:
    exc_type: type[BaseException]
    exc_value: BaseException
    traceback: TracebackType


@dataclass
class TestResult:
    name: TestName
    duration: float
    result: PassedResult | FailedResult
