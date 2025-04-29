from collections.abc import Callable, Generator
from dataclasses import dataclass
from inspect import isgeneratorfunction
from pathlib import Path
from typing import Any, Self, cast, override


class TestPath:
    """Canonical representation of a test path.
    Note this doesn't do any validation using IO, only ensures the
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
class FixtureState:
    generator: Generator[Any] | None
    return_value: Any


class NoNextValue:
    pass


class ParamState[T]:
    def __init__(self, func: Callable[..., list[T]]) -> None:
        self.list = func()
        self.iterator = iter(self.list)


class Test:
    def __init__(self, fqtn: FQTN, test_func: Callable[[], None]) -> None:
        # TODO: maybe I should have a type alias for fixture functions (and also for test functions?)
        self.fqtn = fqtn
        self.test_func = test_func

        self._loaded_fixtures: dict[
            Callable[..., Generator[Any] | Any], FixtureState
        ] = {}
        self._loaded_params: dict[Callable[..., list[Any]], ParamState[Any]] = {}

    def run(self) -> None:
        e = None
        try:
            self.test_func()
        except Exception as err:
            # TODO: log the exception
            e = err
        finally:
            for func, state in self._loaded_fixtures.items():
                if state.generator is not None:
                    try:
                        next(state.generator)
                    except StopIteration:
                        pass
                    else:
                        # TODO: would be nice if we had fqtn for fixtures, too;
                        # We could use is it in error messages like this one
                        # TODO: test that proves this works
                        msg = f"Fixture {func.__name__} has multiple yields"
                        raise ValueError(msg)
            if e is not None:
                raise e

    def load_fixture[T](self, fixture_func: Callable[[], Generator[T] | T]) -> T:
        if fixture_func in self._loaded_fixtures:
            return self._loaded_fixtures[fixture_func].return_value

        if isgeneratorfunction(fixture_func):
            generator = fixture_func()
            return_value = next(generator)
        else:
            generator = None
            # We checked using isgeneratorfunction that the fixture is not a generator
            # So we know the return value is `T`. However, the type checker doesn't
            # seem to figure that out, so we cast to make it happy
            return_value = cast("T", fixture_func())
        self._loaded_fixtures[fixture_func] = FixtureState(
            generator=generator, return_value=return_value
        )
        return return_value

    def load_params[T](self, params_func: Callable[[], list[T]]) -> T:
        if params_func not in self._loaded_params:
            self._loaded_params[params_func] = ParamState(params_func)
        return next(self._loaded_params[params_func].iterator)
