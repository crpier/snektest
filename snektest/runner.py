import inspect
from collections.abc import Callable

from snektest.models import FQTN, Test, TestPath


class TestSession:
    def __init__(self) -> None:
        self._tests: dict[Test, Callable[..., None]] = {}

    def run_tests(self) -> None:
        for test in self._tests.values():
            test()

    def register_test(self, test_func: Callable[..., None]) -> None:
        frame = inspect.currentframe()
        outer_frames = inspect.getouterframes(frame)
        test_path: TestPath = outer_frames[2].frame.f_globals["test_path"]
        test_path.func_name = test_func.__name__
        fqtn = FQTN.from_attributes(
            file=test_path.file,
            class_name=test_path.class_name,
            func_name=test_func.__name__,
        )
        test = Test(func=test_func, fqtn=fqtn, params=None)
        self._tests[test] = test_func


global_session = TestSession()
