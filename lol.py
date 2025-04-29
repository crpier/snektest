import inspect
from collections.abc import Awaitable
from typing import Any, TypeIs, assert_type


def isawaitable(x: object) -> TypeIs[Awaitable[Any]]:
    return inspect.isawaitable(x)


def f(x: Awaitable[int] | int) -> None:
    if isawaitable(x):
        # Type checkers may also infer the more precise type
        # "Awaitable[int] | (int & Awaitable[Any])"
        _ = assert_type(x, Awaitable[int])
    else:
        _ = assert_type(x, int)
