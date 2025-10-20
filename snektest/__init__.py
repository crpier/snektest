from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from typing import Any

from snektest.models import Param
from snektest.runner import global_session


def test[T]() -> Callable[[Callable[[], T]], Callable[[], T]]:
    def decorator(test_func: Callable[[], T]) -> Callable[[], T]:
        global_session.register_test(test_func)  # pyright: ignore[reportArgumentType]
        return test_func

    return decorator


# TODO: make sure that if 2 fixtures with the same name in different test files are called appropriately
def load_fixture[T](
    fixture: Callable[[], Generator[T] | T],
) -> T:
    return global_session.load_fixture(fixture)


async def aload_fixture[T](
    fixture: Callable[[], AsyncGenerator[T] | Coroutine[Any, Any, T]],
) -> T:
    return await global_session.aload_fixture(fixture)


def load_params[T](
    params_func: Callable[[], list[Param[T]] | list[T] | Generator[T]],
) -> T:
    return global_session.load_params(params_func)
