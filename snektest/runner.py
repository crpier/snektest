from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from inspect import currentframe, getouterframes, isgeneratorfunction
from typing import Any, Literal, cast

from snektest.models import FQTN, TestPath


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


@dataclass
class TestResult:
    fqtn: FQTN
    param_names: list[str]
    status: Literal["passed", "failed"] | None = None
    message: str | None = None

    def __str__(self) -> str:
        param_names = "-".join(self.param_names)
        return f"{self.fqtn}[{param_names}] {self.status}"


class Test:
    def __init__(self, fqtn: FQTN, test_func: Callable[[], None]) -> None:
        # TODO: maybe I should have a type alias for fixture functions (and also for test functions?)
        self.fqtn = fqtn
        self.test_func = test_func

        self._loaded_fixtures: dict[
            Callable[..., Generator[Any] | Any], FixtureState
        ] = {}
        self._loaded_params: dict[Callable[..., list[Any]], ParamState[Any]] = {}
        self.runs = 1

    def run(self) -> None:
        e = None
        global_session.results[self] = []

        while self.runs > 0:
            test_result = TestResult(
                fqtn=self.fqtn,
                param_names=[],
                status=None,
            )
            try:
                global_session.results[self].append(test_result)
                self.test_func()
                test_result.status = "passed"
            except Exception as err:
                # TODO: log the exception
                e = err
                test_result.status = "failed"
                test_result.message = str(err)
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
                print(str(test_result))
                if e is not None:
                    raise e
            self.runs -= 1

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

    # TODO: what should happen if we call load_params twice on the same function in a single test?
    def load_params[T](self, params_func: Callable[[], list[T]]) -> T:
        if params_func not in self._loaded_params:
            self._loaded_params[params_func] = ParamState(params_func)
            self.runs += len(self._loaded_params[params_func].list) - 1
        result = next(self._loaded_params[params_func].iterator)
        # Some nice viewing optimizations
        if isinstance(result, Sequence):
            global_session.results[self][-1].param_names.append(
                # Doesn't matter what type T is: it only matters that it's a Sequence,
                # and we can call __str__ o its elements
                "-".join(str(x) for x in result)  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]
            )
        else:
            global_session.results[self][-1].param_names.append(str(result))
        # TODO: repro this in the playground

        # No idea why pyright decided `result` isn't `Any` anymore, but it happened
        # because of the `isinstance` check above
        return result  # pyright: ignore[reportUnknownVariableType,reportReturnType]


class TestSession:
    def __init__(self) -> None:
        self._tests: dict[Callable[[], None], Test] = {}
        self.results: dict[Test, list[TestResult]] = {}

    def run_tests(self) -> None:
        for test in self._tests.values():
            test.run()

    def register_test(self, test_func: Callable[[], None]) -> None:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        test_path: TestPath = outer_frames[2].frame.f_globals["test_path"]
        test_path.func_name = test_func.__name__
        fqtn = FQTN.from_attributes(
            file=test_path.file,
            class_name=test_path.class_name,
            func_name=test_func.__name__,
        )
        if test_func in self._tests:
            # TODO: can this happen? How can I test it?
            msg = f"Test {fqtn} has already been registered"
            raise ValueError(msg)
        self._tests[test_func] = Test(fqtn=fqtn, test_func=test_func)

    def load_fixture[T](self, fixture_func: Callable[[], Generator[T] | T]) -> T:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        function_frame = outer_frames[2]
        test_func = outer_frames[2].frame.f_globals[function_frame.frame.f_code.co_name]
        if test_func not in self._tests:
            msg = f"Test {test_func} has not been registered"
            raise ValueError(msg)
        test = self._tests[test_func]
        return test.load_fixture(fixture_func)

    def load_params[T](self, params_func: Callable[[], list[T]]) -> T:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        function_frame = outer_frames[2]
        test_func = outer_frames[2].frame.f_globals[function_frame.frame.f_code.co_name]
        if test_func not in self._tests:
            msg = f"Test {test_func} has not been registered"
            raise ValueError(msg)
        test = self._tests[test_func]
        return test.load_params(params_func)


global_session = TestSession()
