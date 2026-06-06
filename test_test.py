"""Scratch test showing fixture return type aliases."""

from collections.abc import Coroutine
from typing import assert_type

from snektest import (
    AsyncFixture,
    AsyncSessionFixture,
    Fixture,
    SessionFixture,
    assert_eq,
    load_fixture,
    test,
)


def provide_number() -> Fixture[int]:
    yield 1


async def provide_word() -> AsyncFixture[str]:
    yield "word"


def provide_session_number() -> SessionFixture[int]:
    yield 2


async def provide_async_session_word() -> AsyncSessionFixture[str]:
    yield "session word"


@test(mark="fast")
async def test_fixture_return_type_aliases() -> None:
    number = load_fixture(provide_number())
    word = load_fixture(provide_word())
    session_number = load_fixture(provide_session_number())
    session_word = load_fixture(provide_async_session_word())

    _ = assert_type(number, int)
    _ = assert_type(word, Coroutine[None, None, str])
    _ = assert_type(session_number, int)
    _ = assert_type(session_word, Coroutine[None, None, str])

    assert_eq(number, 1)
    assert_eq(await word, "word")
    assert_eq(session_number, 2)
    assert_eq(await session_word, "session word")
