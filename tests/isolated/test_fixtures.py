from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any, cast

from snektest import assert_raises, load_fixture, session_fixture, test
from snektest.fixtures import (
    get_registered_session_fixtures,
    load_function_fixture,
    load_session_fixture,
)
from snektest.models import UnreachableError


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
async def test_session_fixture_asyncgen_unexpected_registry_state() -> None:
    @session_fixture()
    async def fx() -> AsyncGenerator[int]:
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
