from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from snektest import (
    assert_eq,
    assert_is,
    assert_is_not,
    assert_raises,
    fixture,
    load_fixture,
    test,
)
from snektest.fixtures import FixtureRegistry, use_registry
from snektest.models import FixtureError


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


@test()
async def test_function_fixture_depending_on_function_fixture() -> None:
    """A function fixture may load another; the dependency tears down last."""
    order: list[str] = []

    @fixture
    def inner() -> Generator[str]:
        order.append("inner setup")
        yield "inner-value"
        order.append("inner teardown")

    @fixture
    def outer() -> Generator[str]:
        order.append("outer setup")
        dependency = load_fixture(inner())
        yield f"outer-around-{dependency}"
        order.append("outer teardown")

    registry = FixtureRegistry()
    with use_registry(registry):
        value = load_fixture(outer())
        failures = await registry.teardown_function_fixtures()

    assert_eq(value, "outer-around-inner-value")
    assert_eq(failures, [])
    # The depending fixture is torn down before its dependency, so it may still
    # use the dependency during teardown.
    assert_eq(
        order,
        ["outer setup", "inner setup", "outer teardown", "inner teardown"],
    )


@test()
async def test_async_function_fixture_depending_on_function_fixture() -> None:
    """Async function fixtures follow the same dependency teardown order."""
    order: list[str] = []

    @fixture
    async def inner() -> AsyncGenerator[str]:
        order.append("inner setup")
        yield "inner-value"
        order.append("inner teardown")

    @fixture
    async def outer() -> AsyncGenerator[str]:
        order.append("outer setup")
        dependency = await load_fixture(inner())
        yield f"outer-around-{dependency}"
        order.append("outer teardown")

    registry = FixtureRegistry()
    with use_registry(registry):
        value = await load_fixture(outer())
        failures = await registry.teardown_function_fixtures()

    assert_eq(value, "outer-around-inner-value")
    assert_eq(failures, [])
    assert_eq(
        order,
        ["outer setup", "inner setup", "outer teardown", "inner teardown"],
    )


@test()
async def test_session_fixture_depending_on_session_fixture() -> None:
    """A session fixture may load another; the dependency tears down last."""
    order: list[str] = []

    @fixture(scope="session")
    def inner() -> Generator[str]:
        order.append("inner setup")
        yield "inner-value"
        order.append("inner teardown")

    @fixture(scope="session")
    def outer() -> Generator[str]:
        order.append("outer setup")
        dependency = load_fixture(inner())
        yield f"outer-around-{dependency}"
        order.append("outer teardown")

    registry = FixtureRegistry()
    with use_registry(registry):
        value = load_fixture(outer())
        failures = await registry.teardown_session_fixtures()

    assert_eq(value, "outer-around-inner-value")
    assert_eq(failures, [])
    assert_eq(
        order,
        ["outer setup", "inner setup", "outer teardown", "inner teardown"],
    )


@test()
def test_function_fixture_reuses_cached_session_dependency() -> None:
    """A function fixture depending on a session fixture reuses the cache."""
    setups = 0

    @fixture(scope="session")
    def shared() -> Generator[object]:
        nonlocal setups
        setups += 1
        yield object()

    @fixture
    def consumer() -> Generator[object]:
        yield load_fixture(shared())

    with use_registry(FixtureRegistry()):
        first = load_fixture(consumer())
        second = load_fixture(consumer())

    assert_is(first, second)
    assert_eq(setups, 1)


@test()
def test_session_fixture_cannot_depend_on_function_fixture() -> None:
    """Session fixtures may not load function fixtures (they would outlive them)."""

    @fixture
    def function_dependency() -> Generator[str]:
        yield "value"

    @fixture(scope="session")
    def session_fixture() -> Generator[str]:
        yield load_fixture(function_dependency())

    with (
        use_registry(FixtureRegistry()),
        assert_raises(FixtureError) as exc_info,
    ):
        _ = load_fixture(session_fixture())

    assert_eq(
        "cannot depend on function fixture function_dependency"
        in str(exc_info.exception),
        True,
    )


@test()
async def test_async_session_fixture_cannot_depend_on_function_fixture() -> None:
    """Async session fixtures may not load function fixtures either."""

    @fixture
    def function_dependency() -> Generator[str]:
        yield "value"

    @fixture(scope="session")
    async def session_fixture() -> AsyncGenerator[str]:
        yield load_fixture(function_dependency())

    with (
        use_registry(FixtureRegistry()),
        assert_raises(FixtureError) as exc_info,
    ):
        _ = await load_fixture(session_fixture())

    assert_eq(
        "cannot depend on function fixture function_dependency"
        in str(exc_info.exception),
        True,
    )
