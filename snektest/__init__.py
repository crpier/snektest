from collections.abc import Callable, Generator

from snektest.models import Param
from snektest.runner import global_session


def test() -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorator(test_func: Callable[[], None]) -> Callable[[], None]:
        global_session.register_test(test_func)
        return test_func

    return decorator


# TODO: make sure that if 2 fixtures with the same name in different test files are called appropriately
def load_fixture[T](fixture: Callable[[], Generator[T] | T]) -> T:
    return global_session.load_fixture(fixture)


def load_params[T](
    params_func: Callable[[], list[Param[T]] | list[T] | Generator[T]],
) -> T:
    return global_session.load_params(params_func)
