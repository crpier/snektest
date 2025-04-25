from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self, override


class TestPath:
    """Canonical representation of a test path.
    Note that here we don't do any validation with IO, we only ensure the
    "shape" of the data is valid."""

    def __init__(self, raw_path: str) -> None:
        if "::" not in raw_path:
            file = Path(raw_path)
            class_name = None
            func_name = None

        else:
            file_part, rest = raw_path.split("::", 1)
            file = Path(file_part)
            if file.suffix != ".py":
                msg = f"Invalid TestPath: the file is not a python file: {raw_path}"
                raise ValueError(msg)
            if "::" in rest:
                class_name, func_name = rest.split("::", 1)
            else:
                class_name = None
                func_name = rest

        if file.suffix != ".py":
            msg = f"Invalid TestPath: {raw_path}"
            raise ValueError(msg)
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
        self.file = file
        self.class_name = class_name
        self.func_name = func_name

    @override
    def __str__(self) -> str:
        """This should provide the same result as the raw path given to the init"""
        if self.class_name is None:
            if self.func_name is None:
                return f"{self.file}"
            return f"{self.file}::{self.func_name}"
        if self.func_name is None:
            return f"{self.file}::{self.class_name}"
        return f"{self.file}::{self.func_name}::{self.class_name}"


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


@dataclass
class Test:
    func: Callable[..., None]
    fqtn: FQTN
    params: tuple[tuple[Any]] | None

    @override
    def __hash__(self) -> int:
        return hash(self.func) + hash(self.fqtn) + hash(self.params)
