from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

from snektest import (
    AsyncSessionFixture,
    SessionFixture,
    assert_eq,
    assert_raises,
    load_fixture,
    test,
)
from snektest.fixtures import (
    get_registered_session_fixtures,
    load_function_fixture,
    load_session_fixture,
)
from snektest.models import FixtureError, UnreachableError


@test()
def test_load_function_fixture_rejects_non_generator() -> None:
    with assert_raises(UnreachableError):
        _ = load_function_fixture(cast("Any", 123))


@test()
def test_load_session_fixture_unregistered_raises() -> None:
    def gen() -> Generator[int]:
        yield 1

    with assert_raises(UnreachableError):
        _ = load_session_fixture(gen())


@test()
def test_session_fixture_rejects_parameters() -> None:
    def fx(value: int) -> SessionFixture[int]:
        yield value

    with assert_raises(FixtureError) as exc_info:
        _ = load_fixture(fx(1))

    assert_eq(
        str(exc_info.exception),
        "Session fixture test_session_fixture_rejects_parameters.<locals>.fx cannot accept parameters: value. Session fixtures are cached once per fixture function; use a function fixture for parameter-dependent setup, or return a factory/cache from a zero-argument session fixture.",
    )


@test()
def test_async_session_fixture_rejects_optional_parameters() -> None:
    async def fx(value: int = 1) -> AsyncSessionFixture[int]:
        yield value

    with assert_raises(FixtureError) as exc_info:
        _ = load_fixture(fx())

    assert_eq(
        str(exc_info.exception),
        "Session fixture test_async_session_fixture_rejects_optional_parameters.<locals>.fx cannot accept parameters: value. Session fixtures are cached once per fixture function; use a function fixture for parameter-dependent setup, or return a factory/cache from a zero-argument session fixture.",
    )


@test()
async def test_session_fixture_asyncgen_unexpected_registry_state() -> None:
    async def fx() -> AsyncSessionFixture[int]:
        yield 1

    awaitable = load_fixture(fx())

    def bad_gen() -> Generator[int]:
        yield 1

    registry = get_registered_session_fixtures()
    try:
        registry[fx.__code__] = (bad_gen(), awaitable)
        with assert_raises(UnreachableError):
            _ = await awaitable
    finally:
        _ = registry.pop(fx.__code__, None)
