from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator

from snektest import (
    Scope,
    assert_eq,
    assert_in,
    assert_is,
    assert_is_none,
    assert_is_not,
    assert_isinstance,
    assert_raises,
    fixture,
    load_fixture,
    test,
)
from snektest.fixtures import FixtureRegistry, use_registry
from snektest.models import FixtureError

_run_fixture_events: list[str] = []
_run_fixture_originals: list[dict[str, str]] = []


@fixture(scope="run")
def _run_descriptor() -> Generator[dict[str, str]]:
    _run_fixture_events.append("setup")
    descriptor = {"status": "ready"}
    _run_fixture_originals.append(descriptor)
    yield descriptor
    _run_fixture_events.append("teardown")


@fixture(scope="run")
def _unpickleable_run_descriptor() -> Generator[object]:
    yield lambda: None


@fixture(scope="run")
async def _async_run_descriptor() -> AsyncGenerator[dict[str, str]]:
    yield {"status": "async-ready"}


@fixture(scope="run")
def _run_cycle_a() -> Generator[str]:
    yield load_fixture(_run_cycle_b())


@fixture(scope="run")
def _run_cycle_b() -> Generator[str]:
    yield load_fixture(_run_cycle_a())


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
def test_public_session_scope_enum_uses_session_lifecycle() -> None:
    """The exported enum maps to the same scope as the session string literal."""

    @fixture(scope=Scope.SESSION)
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
def test_public_function_scope_enum_uses_function_lifecycle() -> None:
    """The exported function enum member does not accidentally cache values."""

    @fixture(scope=Scope.FUNCTION)
    def thing() -> Generator[object]:
        yield object()

    with use_registry(FixtureRegistry()):
        first = load_fixture(thing())
        second = load_fixture(thing())

    assert_is_not(first, second)


@test()
async def test_run_fixture_is_lazy_cached_copied_and_torn_down() -> None:
    """Local runs enforce the same descriptor-copy contract as process runs."""
    _run_fixture_events.clear()
    _run_fixture_originals.clear()
    registry = FixtureRegistry()

    with use_registry(registry):
        assert_eq(_run_fixture_events, [])
        first = load_fixture(_run_descriptor())
        second = load_fixture(_run_descriptor())
        failures = await registry.teardown_run_fixtures()

    assert_is(first, second)
    assert_is_not(first, _run_fixture_originals[0])
    assert_eq(first, {"status": "ready"})
    assert_eq(_run_fixture_events, ["setup", "teardown"])
    assert_eq(failures, [])


@test()
async def test_async_run_fixture_publishes_descriptor_copy() -> None:
    registry = FixtureRegistry()
    with use_registry(registry):
        descriptor = await load_fixture(_async_run_descriptor())
        failures = await registry.teardown_run_fixtures()

    assert_eq(descriptor, {"status": "async-ready"})
    assert_eq(failures, [])


@test()
async def test_concurrent_async_run_fixture_loads_share_worker_copy() -> None:
    registry = FixtureRegistry()
    with use_registry(registry):
        first, second = await asyncio.gather(
            load_fixture(_async_run_descriptor()),
            load_fixture(_async_run_descriptor()),
        )
        _ = await registry.teardown_run_fixtures()

    assert_is(first, second)


@test()
async def test_run_fixture_rejects_unpickleable_descriptor() -> None:
    registry = FixtureRegistry()
    with use_registry(registry):
        with assert_raises(FixtureError):
            _ = load_fixture(_unpickleable_run_descriptor())
        failures = await registry.teardown_run_fixtures()

    assert_eq(failures, [])


@test()
def test_run_fixture_rejects_local_and_parameterized_definitions() -> None:
    with assert_raises(TypeError):

        @fixture(scope="run")
        def local_fixture() -> Generator[str]:
            yield "local"

    with assert_raises(TypeError):

        @fixture(scope="run")  # ty: ignore[no-matching-overload]
        def parameterized_fixture(value: str) -> Generator[str]:
            yield value


@test()
def test_public_run_scope_enum_creates_run_fixture() -> None:
    handle = _run_descriptor()

    assert_eq(handle.scope, Scope.RUN.value)


@test()
async def test_run_fixture_dependency_cycle_fails_clearly() -> None:
    registry = FixtureRegistry()
    with use_registry(registry):
        with assert_raises(FixtureError) as raised:
            _ = load_fixture(_run_cycle_a())
        _ = await registry.teardown_run_fixtures()

    assert_in("_run_cycle_a -> _run_cycle_b -> _run_cycle_a", str(raised.exception))


@test()
async def test_fixture_teardown_cannot_reopen_dependency_graph() -> None:
    dependency_setups: list[str] = []

    @fixture
    def dependency() -> Generator[str]:
        dependency_setups.append("setup")
        yield "dependency"

    @fixture
    def owner() -> Generator[str]:
        yield "owner"
        _ = load_fixture(dependency())

    registry = FixtureRegistry()
    with use_registry(registry):
        _ = load_fixture(owner())
        failures = await registry.teardown_function_fixtures()

    assert_eq(dependency_setups, [])
    assert_eq(len(failures), 1)
    assert_eq(failures[0].exception.type_name, "FixtureError")
    assert_in("teardown has started", failures[0].exception.message)


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
async def test_concurrent_async_session_loads_share_setup() -> None:
    """Concurrent first loads await one setup and receive one value."""
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()
    setups = 0

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[object]:
        nonlocal setups
        setups += 1
        setup_started.set()
        await release_setup.wait()
        yield object()

    registry = FixtureRegistry()
    with use_registry(registry):
        first_waiter = asyncio.ensure_future(load_fixture(thing()))
        await setup_started.wait()
        second_waiter = asyncio.ensure_future(load_fixture(thing()))
        release_setup.set()
        first, second = await asyncio.gather(first_waiter, second_waiter)
        failures = await registry.teardown_session_fixtures()

    assert_is(first, second)
    assert_eq(setups, 1)
    assert_eq(failures, [])


@test()
async def test_cancelled_waiter_does_not_cancel_shared_session_setup() -> None:
    """Cancelling one waiter leaves shared setup available to other waiters."""
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[object]:
        setup_started.set()
        await release_setup.wait()
        yield object()

    registry = FixtureRegistry()
    with use_registry(registry):
        cancelled_waiter = asyncio.ensure_future(load_fixture(thing()))
        await setup_started.wait()
        surviving_waiter = asyncio.ensure_future(load_fixture(thing()))
        _ = cancelled_waiter.cancel()
        with assert_raises(asyncio.CancelledError):
            await cancelled_waiter
        release_setup.set()
        loaded = await surviving_waiter
        cached = await load_fixture(thing())
        failures = await registry.teardown_session_fixtures()

    assert_is(loaded, cached)
    assert_eq(failures, [])


@test()
async def test_failed_async_session_setup_can_be_retried() -> None:
    """A setup failure removes its pending cache entry."""
    attempts = 0

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            setup_error = RuntimeError("first setup failed")
            raise setup_error
        yield "ready"

    registry = FixtureRegistry()
    with use_registry(registry):
        with assert_raises(RuntimeError):
            _ = await load_fixture(thing())
        loaded = await load_fixture(thing())
        failures = await registry.teardown_session_fixtures()

    assert_eq(loaded, "ready")
    assert_eq(attempts, 2)
    assert_eq(failures, [])


@test()
async def test_cancelled_async_session_setup_can_be_retried() -> None:
    """A fixture-raised cancellation does not poison the session cache."""
    attempts = 0

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError
        yield "ready"

    registry = FixtureRegistry()
    with use_registry(registry):
        with assert_raises(asyncio.CancelledError):
            _ = await load_fixture(thing())
        loaded = await load_fixture(thing())
        failures = await registry.teardown_session_fixtures()

    assert_eq(loaded, "ready")
    assert_eq(attempts, 2)
    assert_eq(failures, [])


@test()
async def test_session_teardown_cancels_pending_async_setup() -> None:
    """Registry teardown cancels setup that no test is still able to use."""
    setup_started = asyncio.Event()
    setup_cancelled = asyncio.Event()
    never_release = asyncio.Event()

    @fixture(scope="session")
    async def thing() -> AsyncGenerator[str]:
        setup_started.set()
        try:
            await never_release.wait()
            yield "unreachable"
        finally:
            setup_cancelled.set()

    registry = FixtureRegistry()
    with use_registry(registry):
        waiter = asyncio.ensure_future(load_fixture(thing()))
        await setup_started.wait()
        failures = await registry.teardown_session_fixtures()
        with assert_raises(asyncio.CancelledError):
            _ = await waiter

    assert_eq(setup_cancelled.is_set(), True)
    assert_eq(failures, [])


@test()
async def test_session_setup_scope_is_task_local() -> None:
    """Suspended session setup does not constrain an unrelated task."""
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()

    @fixture
    def function_fixture() -> Generator[str]:
        yield "function-value"

    @fixture(scope="session")
    async def session_fixture() -> AsyncGenerator[str]:
        setup_started.set()
        await release_setup.wait()
        yield "session-value"

    registry = FixtureRegistry()
    function_error: FixtureError | None = None
    function_value: str | None = None
    with use_registry(registry):
        session_waiter = asyncio.ensure_future(load_fixture(session_fixture()))
        await setup_started.wait()
        try:
            function_value = load_fixture(function_fixture())
        except FixtureError as exc:
            function_error = exc
        release_setup.set()
        session_value = await session_waiter
        function_failures = await registry.teardown_function_fixtures()
        session_failures = await registry.teardown_session_fixtures()

    assert_is_none(function_error)
    assert_eq(function_value, "function-value")
    assert_eq(session_value, "session-value")
    assert_eq(function_failures, [])
    assert_eq(session_failures, [])


@test()
async def test_interleaved_session_setup_still_rejects_function_dependency() -> None:
    """Another setup task cannot clear a session fixture's scope state."""
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    first_finished = asyncio.Event()

    @fixture
    def function_dependency() -> Generator[str]:
        yield "function-value"

    @fixture(scope="session")
    async def first_session() -> AsyncGenerator[str]:
        first_started.set()
        await release_first.wait()
        yield "first-value"

    @fixture(scope="session")
    async def second_session() -> AsyncGenerator[str]:
        second_started.set()
        await first_finished.wait()
        yield load_fixture(function_dependency())

    registry = FixtureRegistry()
    dependency_error: FixtureError | None = None
    with use_registry(registry):
        first_waiter = asyncio.ensure_future(load_fixture(first_session()))
        await first_started.wait()
        second_waiter = asyncio.ensure_future(load_fixture(second_session()))
        await second_started.wait()
        release_first.set()
        _ = await first_waiter
        first_finished.set()
        try:
            _ = await second_waiter
        except FixtureError as exc:
            dependency_error = exc
        failures = await registry.teardown_session_fixtures()

    dependency_error = assert_isinstance(dependency_error, FixtureError)
    assert_eq(
        "cannot depend on function fixture function_dependency"
        in str(dependency_error),
        True,
    )
    assert_eq(failures, [])


@test()
async def test_async_session_dependency_tears_down_after_dependent() -> None:
    """Async session dependencies retain reverse dependency teardown order."""
    order: list[str] = []

    @fixture(scope="session")
    async def inner() -> AsyncGenerator[str]:
        order.append("inner setup")
        yield "inner-value"
        order.append("inner teardown")

    @fixture(scope="session")
    async def outer() -> AsyncGenerator[str]:
        order.append("outer setup")
        dependency = await load_fixture(inner())
        yield f"outer-around-{dependency}"
        order.append("outer teardown")

    registry = FixtureRegistry()
    with use_registry(registry):
        loaded = await load_fixture(outer())
        failures = await registry.teardown_session_fixtures()

    assert_eq(loaded, "outer-around-inner-value")
    assert_eq(failures, [])
    assert_eq(
        order,
        ["outer setup", "inner setup", "outer teardown", "inner teardown"],
    )


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
