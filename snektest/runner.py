import random
import string
import traceback
from asyncio import iscoroutinefunction
from collections.abc import AsyncGenerator as _AsyncGenerator
from collections.abc import Coroutine
from collections.abc import Generator as _Generator
from dataclasses import dataclass
from inspect import isasyncgen
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterator,
    Literal,
    ParamSpec,
    Tuple,
    TypeVar,
    TypeVarTuple,
    Unpack,
)

from snek.snektest.presentation import Output
from snek.snektest.results import TestResult, TestStatus, show_results

T = TypeVar("T")
T2 = TypeVarTuple("T2")
P = ParamSpec("P")
Generator = _Generator[T, None, None]
AsyncGenerator = _AsyncGenerator[T, None]


FixtureScope = Literal["test", "session"]


class RegisteredFixture:
    name: str
    function: Callable[..., Generator]
    scope: FixtureScope
    fixture_params: list[tuple[Any]]

    def __init__(
        self,
        name: str,
        function: Callable[..., Generator],
        scope: FixtureScope,
        fixture_params: list[tuple] | None = None,
    ):
        self.name = name
        self.function = function
        self.scope = scope
        if fixture_params is None:
            fixture_params = []
        self.fixture_params = fixture_params
        self._stop_registering = False
        self._params_idx = 0

    def register_params(self, fixture_param: tuple[Any]) -> None:
        if self._stop_registering:
            raise ValueError("Cannot register more params loading the fixture")
        # This happens when a fixture is parametrized
        # TODO: will this ever be None?
        if self.fixture_params is None:
            self.fixture_params = [fixture_param]
        else:
            self.fixture_params.append(fixture_param)


# TODO: somehow make this read only after we're done registering
class RegisteredFixturesContainer:
    def __init__(self):
        self._registered_fixtures: dict[Callable, RegisteredFixture] = {}

    def register_fixture(
        self,
        func: Callable[..., Generator],
        fixture_param: tuple[Any],
        scope: FixtureScope = "test",
    ):
        name = func.__name__
        if func not in self._registered_fixtures:
            self._registered_fixtures[func] = RegisteredFixture(
                name, func, scope, [fixture_param]
            )
        else:
            self._registered_fixtures[func].register_params(fixture_param)

    def __iter__(self) -> Iterator[RegisteredFixture]:
        return iter(self._registered_fixtures.values())

    def get_by_function(self, func: Callable) -> RegisteredFixture | None:
        return self._registered_fixtures.get(func)

    def get_by_function_strict(self, func: Callable) -> RegisteredFixture:
        return self._registered_fixtures[func]


@dataclass
class RegisteredTest:
    func: Callable[..., None]
    test_name: str
    test_params: list[tuple[Any]]

    def register_params(self, test_params: list[tuple[Any]]):
        self.test_params.extend(test_params)


class RegisteredTestsContainer:
    def __init__(self):
        self.registered_tests: dict[Callable, RegisteredTest] = {}

    def register_test(
        self,
        func: Callable[..., None],
        test_params: tuple,
    ):
        """Allow registering a test multipe times with different params"""
        test_params_to_add: list[tuple[Any]]
        # params is be empty if the test is not parametrized
        if len(test_params) == 0:
            test_params_to_add = []
        else:
            test_params_to_add = [test_params]

        if func not in self.registered_tests:
            self.registered_tests[func] = RegisteredTest(
                func, func.__name__, test_params_to_add
            )
        else:
            self.registered_tests[func].register_params(test_params_to_add)

    def __iter__(self) -> Iterator[RegisteredTest]:
        return iter(self.registered_tests.values())

    def get_by_function(self, func: Callable) -> RegisteredTest | None:
        return self.registered_tests.get(func)

    def get_by_function_strict(self, func: Callable) -> RegisteredTest:
        return self.registered_tests[func]


class TestSession:
    def __init__(self):
        self.tests = RegisteredTestsContainer()
        self.fixtures = RegisteredFixturesContainer()

    def register_test_instance(
        self, new_test: Callable[..., None], test_params: tuple
    ) -> None:
        self.tests.register_test(new_test, test_params)

    def register_fixture(
        self,
        func: Callable[..., Generator],
        fixture_params: tuple,
        scope: FixtureScope = "test",
    ):
        self.fixtures.register_fixture(func, fixture_params, scope)

    async def run_tests(
        self, tests: list[Callable] | None = None, verbose: bool = False
    ) -> None:
        test_results: dict[str, TestResult] = {}
        if tests is None:
            tests_to_run = self.tests
        else:
            # TODO: decide on a more general level: look before you leap or try/except
            tests_to_run = [self.tests.get_by_function_strict(func) for func in tests]
        global output
        output = Output(verbose)

        for test in tests_to_run:
            global test_runner
            test_runner = TestRunner(
                fixtures=self.fixtures,
                test_func=test.func,
                test_params=test.test_params,
                test_name=test.test_name,
            )
            results = await test_runner.run_test()
            # TODO: this should contain test params instead
            for status, message in results:
                test_results[test.test_name + random_string(5)] = TestResult(
                    status=status, message=message
                )
            test_runner = None

        show_results(test_results)


def random_string(length: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class LoadedFixture:
    fixture_func: Callable
    generator: Generator | AsyncGenerator | None
    # TODO: do I use this anywhere anymore?
    last_result: Any
    params: list[tuple[Any]]

    def __init__(self, fixture_func: Callable, params: list[tuple[Any]]):
        self.fixture_func = fixture_func
        self.generator = None
        self.last_result = None
        self.params = params
        self._params_idx = -1
        self._can_reset_params = False

    def next_params(self) -> tuple[Any]:
        if len(self.params) == 0:
            return tuple()
        else:
            self._params_idx += 1
            if self._params_idx > len(self.params):
                # We should peek before trying to load more params
                raise ValueError("Tried to load more params than there are")
            return self.params[self._params_idx]

    def has_next_param(self) -> bool:
        if len(self.params) == 0:
            return False
        else:
            if self._params_idx + 1 >= len(self.params):
                return False
            return True

    def reset_params(self) -> None:
        self._params_idx = -1
        self._can_reset_params = False
        self.generator = None
        self.last_result = None

    def set_can_reset_params(self, can_reset_params: bool) -> None:
        self._can_reset_params = can_reset_params

    def can_reset_params(self) -> bool:
        return self._can_reset_params


class LoadedFixturesContainer:
    def __init__(self, registered_fixtures: RegisteredFixturesContainer):
        self.registered_fixtures = registered_fixtures
        self.preloaded_fixtures: dict[Callable, LoadedFixture] = {}
        self._can_generate_new_value = True
        self._has_next_param = False

    def __contains__(self, fixture_func: Callable) -> bool:
        return fixture_func in self.preloaded_fixtures

    async def teardown_fixtures(self, test_name: str) -> str:
        message = ""
        for fixture in self.preloaded_fixtures.values():
            try:
                if fixture.generator is not None:
                    if isasyncgen(fixture.generator):
                        await anext(fixture.generator)
                    else:
                        next(fixture.generator)
                raise ValueError(
                    f"Fixture {fixture.fixture_func} for test {test_name} has more than one 'yield'"
                )
            except (StopIteration, StopAsyncIteration):
                pass
            except Exception:
                message += f"Unexpected error tearing down fixture {fixture.fixture_func} for test {test_name}: \n{traceback.format_exc()}\n"
        return message

    # TODO: preload fixtures lazily
    def _preload_fixture(self, fixture_data: RegisteredFixture) -> None:
        if fixture_data.function in self.preloaded_fixtures:
            raise ValueError(f"Fixture {fixture_data.name} has already been preloaded")

        self.preloaded_fixtures[fixture_data.function] = LoadedFixture(
            fixture_func=fixture_data.function,
            params=fixture_data.fixture_params,
        )

    @property
    def loaded_fixtures(self) -> dict[Callable, LoadedFixture]:
        return {
            func: fixture
            for func, fixture in self.preloaded_fixtures.items()
            if fixture.generator is not None
        }

    def can_reset_params(self, fixture_func: Callable) -> bool:
        for fixture in self.loaded_fixtures.values():
            if fixture.fixture_func is fixture_func:
                continue
            if fixture.has_next_param():
                return True
        return False

    def get_loaded_fixture_by_function(self, func: Callable) -> LoadedFixture | None:
        return self.preloaded_fixtures.get(func)

    def get_loaded_fixture_by_function_strict(self, func: Callable) -> LoadedFixture:
        return self.preloaded_fixtures[func]

    def next_loaded_fixture(self, fixture_func: Callable) -> LoadedFixture | None:
        # TODO: this is so wasteful
        try:
            fixture_index = list(self.loaded_fixtures.keys()).index(fixture_func)
            next_fixture_func = list(self.loaded_fixtures.keys())[fixture_index + 1]
            return self.loaded_fixtures[next_fixture_func]
        except IndexError:
            return None

    def load_fixture(self, fixture_func: Callable[..., Generator[T]]) -> T:
        fixture = self.get_loaded_fixture_by_function(fixture_func)
        if fixture is None:
            self._preload_fixture(
                self.registered_fixtures.get_by_function_strict(fixture_func)
            )
            fixture = self.get_loaded_fixture_by_function_strict(fixture_func)

        if fixture.generator is None:
            fixture.generator = fixture_func(*fixture.next_params())
            self._can_generate_new_value = False
        elif self._can_generate_new_value:
            if fixture.has_next_param():
                fixture.generator = fixture_func(*fixture.next_params())
                self._can_generate_new_value = False
            else:
                if fixture.can_reset_params():
                    fixture.reset_params()
                    fixture.generator = fixture_func(*fixture.next_params())
                else:
                    return fixture.last_result
        else:
            # generator is not None (this isn't the first load for this fixture)
            # and we can't generate a new value
            if fixture.has_next_param():
                self._has_next_param = True
            return fixture.last_result

        if fixture.has_next_param():
            self._has_next_param = True
        else:
            if (
                next_fixture := self.next_loaded_fixture(fixture_func)
            ) is not None and next_fixture.has_next_param():
                fixture.set_can_reset_params(True)
        fixture.last_result = next(fixture.generator)
        return fixture.last_result

    async def load_fixture_async(
        self, fixture_func: Callable[..., AsyncGenerator[T]]
    ) -> T:
        fixture = self.get_loaded_fixture_by_function(fixture_func)
        if fixture is None:
            self._preload_fixture(
                self.registered_fixtures.get_by_function_strict(fixture_func)
            )
            fixture = self.get_loaded_fixture_by_function_strict(fixture_func)

        if fixture.generator is None:
            fixture.generator = fixture_func(*fixture.next_params())
            self._can_generate_new_value = False
        elif self._can_generate_new_value:
            if fixture.has_next_param():
                fixture.generator = fixture_func(*fixture.next_params())
                self._can_generate_new_value = False
            else:
                if fixture.can_reset_params():
                    fixture.reset_params()
                    fixture.generator = fixture_func(*fixture.next_params())
                else:
                    return fixture.last_result
        else:
            # generator is not None (this isn't the first load for this fixture)
            # and we can't generate a new value
            if fixture.has_next_param():
                self._has_next_param = True
            return fixture.last_result

        if fixture.has_next_param():
            self._has_next_param = True
        else:
            if (
                next_fixture := self.next_loaded_fixture(fixture_func)
            ) is not None and next_fixture.has_next_param():
                fixture.set_can_reset_params(True)
        if isasyncgen(fixture.generator):
            fixture.last_result = await anext(fixture.generator)
        else:
            fixture.last_result = next(fixture.generator)
        return fixture.last_result


class TestRunner:
    def __init__(
        self,
        fixtures: RegisteredFixturesContainer,
        test_func: Callable,
        test_params: list[tuple[Any]],
        test_name: str,
    ):
        self.fixtures = fixtures
        self.test_func = test_func
        self.test_params = test_params
        self.fixture_params = []
        self.test_name = test_name
        """Fixtures that have been loaded for this test.
        This test includes all variations of the test generated by 
        having multiple test params and multiple fixture params.
        This means that a fixture can be loaded multiple times
        during the lifetime of this class."""
        self.fixture_param_repeats = 1

    async def run_test(self) -> list[Tuple[TestStatus, str]]:
        results: list[Tuple[TestStatus, str]] = []
        for test_params in self.test_params or [tuple()]:
            # TODO: instead of passing RegisteredFixturesContainer all around the file,
            # maybe use a global variable?
            loaded_fixtures = LoadedFixturesContainer(self.fixtures)
            global test_instance_runner
            test_instance_runner = TestInstanceRunner(
                loaded_fixtures=loaded_fixtures,
                test_func=self.test_func,
                test_params=test_params,
                test_name=self.test_name,
            )
            result = await test_instance_runner.run_test_instance()
            results.extend(result)
            test_instance_runner = None
        return results


class TestInstanceRunner:
    def __init__(
        self,
        loaded_fixtures: LoadedFixturesContainer,
        test_func: Callable,
        test_params: tuple[Any],
        test_name: str,
    ):
        # TODO: maybe create the LoadedFixturesContainer here
        self.loaded_fixtures = loaded_fixtures
        self.test_func = test_func
        self.test_params = test_params
        self.test_name = test_name
        self.can_run_again = True

    async def run_test_instance(self) -> list[Tuple[TestStatus, str]]:
        results: list[Tuple[TestStatus, str]] = []
        while self.can_run_again:
            try:
                if iscoroutinefunction(self.test_func):
                    await self.test_func(*self.test_params)
                else:
                    self.test_func(*self.test_params)
                # TODO: kind of dislike using TestStatus in this class
                # is there a nice way to not have to use it?
                status, message = TestStatus.passed, "Test passed"
            except AssertionError:
                status, message = TestStatus.failed, traceback.format_exc()
            except Exception:
                status, message = (
                    TestStatus.failed,
                    f"Unexpected error: {traceback.format_exc()}",
                )
            if output is None:
                raise ValueError("Output is not set")
            output.print_test_output(
                test_name=self.test_name,
                test_params=self.test_params,
                test_status=status,
                fixtures={
                    fixture.fixture_func.__name__: fixture.last_result
                    for fixture in self.loaded_fixtures.loaded_fixtures.values()
                },
            )
            message += await self.after_test_instance(self.test_name)
            results.append((status, message))
        return results

    def load_fixture(self, fixture_func: Callable[..., Generator[T]]) -> T:
        return self.loaded_fixtures.load_fixture(fixture_func)

    async def load_fixture_async(
        self, fixture_func: Callable[..., AsyncGenerator[T]]
    ) -> T:
        return await self.loaded_fixtures.load_fixture_async(fixture_func)

    async def after_test_instance(self, test_name: str) -> str:
        self.can_run_again = False
        message = await self.loaded_fixtures.teardown_fixtures(test_name)
        if self.loaded_fixtures._has_next_param:
            self.loaded_fixtures._has_next_param = False
            self.loaded_fixtures._can_generate_new_value = True
            self.can_run_again = True
        return message


test_session = TestSession()
test_runner: TestRunner | None = None
test_instance_runner: TestInstanceRunner | None = None
output: Output | None = None

### PUBLIC API ###


# TODO: if a certain env var set by the runner is not present,
# these functions should be noops
def load_fixture(fixture: Callable[..., _Generator[T, None, None]]) -> T:
    if test_instance_runner is None:
        raise ValueError("load_fixture can only be used inside a test")
    return test_instance_runner.load_fixture(fixture)


# TODO: rename this to something shorter, maybe aload?
async def load_fixture_async(fixture: Callable[..., AsyncGenerator[T]]) -> T:
    if test_instance_runner is None:
        raise ValueError("load_fixture can only be used inside a test")
    return await test_instance_runner.load_fixture_async(fixture)


def test(
    *params: Unpack[T2],
) -> Callable[[Callable[[Unpack[T2]], None]], Callable[[Unpack[T2]], None]]:
    def decorator(test_func: Callable[..., None]) -> Callable[..., None]:
        test_session.register_test_instance(test_func, params)
        return test_func

    return decorator


def test_async(
    *params: Unpack[T2],
) -> Callable[[Callable[[Unpack[T2]], Awaitable[None]]], Callable[[Unpack[T2]], None]]:
    def decorator(test_func: Callable[..., Coroutine]) -> Callable[..., None]:
        test_session.register_test_instance(test_func, params)
        return test_func

    return decorator


def fixture(
    *params: Unpack[T2], scope: FixtureScope = "test"
) -> Callable[
    [Callable[[Unpack[T2]], Generator[T]]], Callable[[Unpack[T2]], Generator[T]]
]:
    def decorator(func: Callable[..., Generator[T]]):
        test_session.register_fixture(func, params, scope)
        return func

    return decorator


def fixture_async(
    *params: Unpack[T2], scope: FixtureScope = "test"
) -> Callable[
    [Callable[[Unpack[T2]], AsyncGenerator[T]]],
    Callable[[Unpack[T2]], AsyncGenerator[T]],
]:
    def decorator(func: Callable[..., AsyncGenerator[T]]):
        test_session.register_fixture(func, params, scope)
        return func

    return decorator
