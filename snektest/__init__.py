from collections.abc import AsyncGenerator, Callable
from collections.abc import Coroutine as _Coroutine
from inspect import currentframe, getouterframes
from typing import Annotated, Any, cast, get_args, get_origin, overload

from snektest.models import Param, Scope
from snektest.utils import (
    mark_test_function,
    register_fixture,
)

type Coroutine[T] = _Coroutine[None, None, T]


@overload
def test() -> Callable[
    [Callable[[], Coroutine[None]]], Callable[[], Coroutine[None]]
]: ...


@overload
def test[T](
    param: list[Param[T]],
) -> Callable[[Callable[[T], Coroutine[None]]], Callable[[T], Coroutine[None]]]: ...


@overload
def test[T1, T2](
    param1: list[Param[T1]],
    param2: list[Param[T2]],
) -> Callable[
    [Callable[[T1, T2], Coroutine[None]]],
    Callable[[T1, T2], Coroutine[None]],
]: ...


def test(  # pyright: ignore[reportInconsistentOverload]
    *params: list[Param[Any]],
) -> Callable[
    [Callable[[*tuple[Any, ...]], Coroutine[None]]],
    Callable[[*tuple[Any, ...]], Coroutine[None]],
]:
    def decorator(
        test_func: Callable[[*tuple[Any, ...]], Coroutine[None]],
    ) -> Callable[[*tuple[Any, ...]], Coroutine[None]]:
        mark_test_function(test_func, params)
        return test_func

    return decorator


def _determine_scope(func: Callable[..., Any]) -> Scope:
    if (return_annotation := func.__annotations__.get("return")) is None:
        return Scope.FUNCTION
    if get_origin(return_annotation) is not Annotated:
        return Scope.FUNCTION
    annotation_args = get_args(return_annotation)
    if len(annotation_args) != 2:  # noqa: PLR2004
        return Scope.FUNCTION
    annotation_param = annotation_args[1]
    if isinstance(annotation_param, Scope):
        return annotation_param
    return Scope.FUNCTION


async def load_fixture[R](
    gen: AsyncGenerator[R],
) -> R:
    original_function = cast(
        "Callable[..., Any]",
        gen.ag_frame.f_globals[gen.ag_code.co_name],  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    )
    frame = currentframe()
    outer_frames = getouterframes(frame)
    function_frame = outer_frames[1]
    test_func = cast(
        "Callable[..., Any]",
        function_frame.frame.f_globals[function_frame.frame.f_code.co_name],
    )
    register_fixture(
        test_func, (original_function, _determine_scope(original_function), gen)
    )

    return await anext(gen)
