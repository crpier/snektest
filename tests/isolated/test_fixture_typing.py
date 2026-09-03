"""Scratch test showing fixture handle typing through load_fixture."""

from collections.abc import AsyncGenerator, Coroutine, Generator
from typing import assert_type

from snektest import assert_eq, fixture, load_fixture, test


@fixture
def provide_number() -> Generator[int]:
    yield 1


@fixture
async def provide_word() -> AsyncGenerator[str]:
    yield "word"


@fixture(scope="session")
def provide_session_number() -> Generator[int]:
    yield 2


@fixture(scope="session")
async def provide_async_session_word() -> AsyncGenerator[str]:
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
