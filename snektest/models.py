from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override


class TestPath:
    """Canonical representation of a test path.
    Note this doesn't do any validation using IO, only ensures the
    "shape" of the data is valid."""

    path: Path
    class_name: str | None
    func_name: str | None

    def __init__(self, raw_path: str) -> None:
        if "::" not in raw_path:
            path = Path(raw_path)
            class_name = None
            func_name = None
        else:
            file_part, rest = raw_path.split("::", 1)
            path = Path(file_part)
            if "::" in rest:
                class_name, func_name = rest.split("::", 1)
            else:
                class_name = None
                func_name = rest

        if not path.is_file() or path.suffix != ".py":
            msg = f"Invalid TestPath. File provided is not a python file: {raw_path}"
            raise ValueError(msg)

        if class_name is not None:
            msg = "Invaid TestPath. A path to a directory followed by a class specifier was provided"

        if class_name is not None and class_name == "":
            msg = f"Invalid TestPath: empty class name in path: {raw_path}"
            raise ValueError(msg)
        if class_name is not None and not class_name.isidentifier():
            msg = f"Invalid TestPath: invalid class name in path: {raw_path}"

        if func_name is not None and func_name == "":
            msg = f"Invalid TestPath: empty function name in path: {raw_path}"
            raise ValueError(msg)
        if func_name is not None and not func_name.isidentifier():
            msg = f"Invalid TestPath: invalid function name in path: {raw_path}"
            raise ValueError(msg)
        self.path = path
        self.class_name = class_name
        self.func_name = func_name

    @override
    def __str__(self) -> str:
        """This should provide the same result as the raw path given to the init"""
        if self.class_name is None:
            if self.func_name is None:
                return f"{self.path}"
            return f"{self.path}::{self.func_name}"
        if self.func_name is None:
            return f"{self.path}::{self.class_name}"
        return f"{self.path}::{self.func_name}::{self.class_name}"


class FQTN(TestPath):
    """Fully qualified test name.
    Like TestPath, but function name is mandatory."""

    func_name: str

    @override
    def __init__(self, fqtn: str) -> None:
        super().__init__(fqtn)
        # The comparison is necessary, we set `self.func_name` to str so that
        # users of this class know it's always populated, but before the actual
        # validation it might be `None`
        if self.func_name is None:  # pyright: ignore[reportUnnecessaryComparison]
            msg = f"Invalid FQTN: no function name in path: {fqtn}"
            raise ValueError(msg)

    @classmethod
    def from_attributes(
        cls,
        file: Path,
        class_name: str | None,
        func_name: str,
    ) -> Self:
        new = TestPath(str(file))
        new.class_name = class_name
        new.func_name = func_name
        # PERF: this looks really weird, I imagine all these new allocations
        # can be expensive when there's lots of tests
        return cls(str(new))


TestResult = Literal["passed", "failed"]


@dataclass
class TestReport:
    fqtn: FQTN
    param_names: list[str]
    result: TestResult | None = None
    message: str | None = None

    def full_name(self) -> str:
        param_names = "-".join(self.param_names)
        if len(param_names) == 0:
            return str(self.fqtn)
        return f"{self.fqtn}[{param_names}]"


# TODO: should actually be in models.spy
@dataclass
class Param[T]:
    value: T
    name: str | None = None
