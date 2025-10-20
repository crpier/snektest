from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, Sequence
import traceback
from dataclasses import dataclass
from inspect import (
    currentframe,
    getouterframes,
    isasyncgen,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
)
from types import TracebackType
from typing import Any, cast

from snektest.models import FQTN, Param, TestPath, TestReport
from snektest.presenter import DisplayAdapter


@dataclass
class FixtureState:
    generator: AsyncGenerator[Any] | Generator[Any] | None
    return_value: Any


class NoNextValue:
    pass


class ParamState[T]:
    def __init__(
        self, func: Callable[[], list[Param[T]] | list[T] | Generator[T]]
    ) -> None:
        self.list = func()
        self.iterator = iter(self.list)


class Test:
    def __init__(
        self, fqtn: FQTN, test_func: Callable[[], Coroutine[Any, Any, None] | None]
    ) -> None:
        # TODO: maybe I should have a type alias for fixture functions (and also for test functions?)
        self.fqtn = fqtn
        self.test_func = test_func

        self._loaded_fixtures: dict[
            Callable[..., Generator[Any] | Any], FixtureState
        ] = {}
        self._loaded_params: dict[Callable[..., Any], ParamState[Any]] = {}
        self.runs = 1

    async def run(self) -> None:
        global_session.results[self] = []

        while self.runs > 0:
            test_report = TestReport(
                fqtn=self.fqtn,
                param_names=[],
                result=None,
            )
            try:
                global_session.results[self].append(test_report)
                if iscoroutinefunction(self.test_func):
                    await self.test_func()
                else:
                    # pyright thinks there that the function might be a coroutine
                    # so it wants us to store the result, but we know it can't be
                    self.test_func()  # pyright: ignore[reportUnusedCallResult]
                test_report.result = "passed"
            except Exception as err:
                # TODO: log the exception
                test_report.result = "failed"
                test_report.message = str(err)
                test_report.traceback = traceba
            finally:
                for func, state in self._loaded_fixtures.items():
                    if state.generator is not None:
                        try:
                            if isasyncgen(state.generator):
                                await anext(state.generator)
                            else:
                                next(state.generator)
                        except (StopIteration, StopAsyncIteration):
                            pass
                        else:
                            # TODO: would be nice if we had fqtn for fixtures, too;
                            # We could use is it in error messages like this one
                            # TODO: test that proves this works
                            msg = f"Fixture {func.__name__} has multiple yields"
                            raise ValueError(msg)
                if test_report.result is None:
                    msg = "TestResult.status is None after test"
                    raise ValueError(msg)
                global_session.display_adapter.print_test_result(
                    test_name=test_report.full_name(),
                    test_result=test_report.result,
                )
            self.runs -= 1

    def load_fixture[T](
        self,
        fixture_func: Callable[
            [], Coroutine[Any, Any, T] | AsyncGenerator[T] | Generator[T] | T
        ],
    ) -> T:
        if fixture_func in self._loaded_fixtures:
            return self._loaded_fixtures[fixture_func].return_value

        # sync with teardown
        if isgeneratorfunction(fixture_func):
            generator = fixture_func()
            return_value = next(generator)
        else:
            generator = None
            return_value = cast("T", fixture_func())
        self._loaded_fixtures[fixture_func] = FixtureState(
            generator=generator, return_value=return_value
        )
        return return_value

    async def aload_fixture[T](
        self,
        fixture_func: Callable[
            [], Coroutine[Any, Any, T] | AsyncGenerator[T] | Generator[T] | T
        ],
    ) -> T:
        if fixture_func in self._loaded_fixtures:
            return self._loaded_fixtures[fixture_func].return_value

        # sync with teardown
        if isgeneratorfunction(fixture_func):
            generator = fixture_func()
            return_value = next(generator)
        # async with teardown
        elif isasyncgenfunction(fixture_func):
            generator = fixture_func()
            # TODO: try casting the function instead
            return_value = cast("T", await anext(generator))
        # async without teardown
        elif iscoroutinefunction(fixture_func):
            generator = None
            return_value = cast("T", await fixture_func())
        else:
            generator = None
            return_value = cast("T", fixture_func())
        self._loaded_fixtures[fixture_func] = FixtureState(
            generator=generator, return_value=return_value
        )
        return return_value

    # TODO: what should happen if we call load_params twice on the same function in a single test?
    def load_params[T](
        self, params_func: Callable[[], list[Param[T]] | list[T] | Generator[T]]
    ) -> T:
        if params_func not in self._loaded_params:
            self._loaded_params[params_func] = ParamState(params_func)
            self.runs += len(self._loaded_params[params_func].list) - 1
        result = next(self._loaded_params[params_func].iterator)
        name = None
        if isinstance(result, Param):
            name = result.name
            result = result.value
        if name is not None:
            global_session.results[self][-1].param_names.append(name)
        # Some nice viewing optimization
        elif isinstance(result, Sequence):
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
        self._tests: dict[Callable[[], Coroutine[Any, Any, None] | None], Test] = {}
        self.results: dict[Test, list[TestReport]] = {}
        self.display_adapter = DisplayAdapter()

    async def run_tests(self) -> None:
        for test in self._tests.values():
            await test.run()
        for reports in self.results.values():
            for report in reports:
                if report.result == "failed":
                    print("====")
                    print(report.fqtn)
                    breakpoint()
                    print(report.error.)

    def register_test(
        self, test_func: Callable[[], Coroutine[Any, Any, None] | None]
    ) -> None:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        test_path: TestPath = outer_frames[2].frame.f_globals["test_path"]
        test_path.func_name = test_func.__name__
        fqtn = FQTN.from_attributes(
            file=test_path.path,
            class_name=test_path.class_name,
            func_name=test_func.__name__,
        )
        if test_func in self._tests:
            # TODO: can this happen? How can I test it?
            msg = f"Test {fqtn} has already been registered"
            raise ValueError(msg)
        self._tests[test_func] = Test(fqtn=fqtn, test_func=test_func)

    def load_fixture[T](
        self, fixture_func: Callable[[], AsyncGenerator[T] | Generator[T] | T]
    ) -> T:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        function_frame = outer_frames[2]
        test_func = outer_frames[2].frame.f_globals[function_frame.frame.f_code.co_name]
        if test_func not in self._tests:
            msg = f"Test {test_func} has not been registered"
            raise ValueError(msg)
        test = self._tests[test_func]
        return test.load_fixture(fixture_func)

    async def aload_fixture[T](
        self, fixture_func: Callable[[], AsyncGenerator[T] | Coroutine[Any, Any, T]]
    ) -> T:
        frame = currentframe()
        outer_frames = getouterframes(frame)
        function_frame = outer_frames[2]
        test_func = outer_frames[2].frame.f_globals[function_frame.frame.f_code.co_name]
        if test_func not in self._tests:
            msg = f"Test {test_func} has not been registered"
            raise ValueError(msg)
        test = self._tests[test_func]
        return await test.aload_fixture(fixture_func)

    def load_params[T](
        self, params_func: Callable[[], list[Param[T]] | list[T] | Generator[T]]
    ) -> T:
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
