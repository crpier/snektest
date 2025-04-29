from collections.abc import Callable, Generator
from inspect import currentframe, getouterframes

from snektest.models import FQTN, Test, TestPath


class TestSession:
    def __init__(self) -> None:
        self._tests: dict[Callable[[], None], Test] = {}

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
