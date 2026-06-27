from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from snektest import (
    assert_eq,
    assert_is,
    assert_is_not,
    fixture,
    load_fixture,
    test,
)
from snektest.fixtures import FixtureRegistry, use_registry


@test()
def test_session_fixture_is_cached_within_a_run() -> None:
    """A session fixture is set up once and the same value is reused."""

    @fixture(scope="session")
    def thing() -> Generator[object]:
        yield object()

    with use_registry(FixtureRegistry()):
        first = load_fixture(thing())
        second = load_fixture(thing())

    assert_is(first, second)


@test()
def test_function_fixture_is_fresh_for_each_load() -> None:
    """Function fixtures build a new value on every load."""

    @fixture
    def thing() -> Generator[object]:
        yield object()

    with use_registry(FixtureRegistry()):
        first = load_fixture(thing())
        second = load_fixture(thing())

    assert_is_not(first, second)


@test()
def test_function_fixture_forwards_arguments() -> None:
    """Arguments passed at the load site reach the fixture body."""

    @fixture
    def make_user(name: str) -> Generator[dict[str, str]]:
        yield {"name": name}

    with use_registry(FixtureRegistry()):
        ada = load_fixture(make_user("Ada"))
        bob = load_fixture(make_user("Bob"))

    assert_eq(ada["name"], "Ada")
    assert_eq(bob["name"], "Bob")


@test()
async def test_function_fixtures_tear_down_in_reverse_order() -> None:
    """Function fixtures are torn down first-in-last-out after the test."""
    order: list[str] = []

    @fixture
    def first() -> Generator[None]:
        yield
        order.append("first")

    @fixture
    def second() -> Generator[None]:
        yield
        order.append("second")

    registry = FixtureRegistry()
    with use_registry(registry):
        _ = load_fixture(first())
        _ = load_fixture(second())
        failures = await registry.teardown_function_fixtures()

    assert_eq(failures, [])
    assert_eq(order, ["second", "first"])


@test()
async def test_async_session_fixture_is_cached_within_a_run() -> None:
    """Async session fixtures are awaited once and reused across loads."""

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[object]:
        yield object()

    with use_registry(FixtureRegistry()):
        first = await load_fixture(thing())
        second = await load_fixture(thing())

    assert_is(first, second)
