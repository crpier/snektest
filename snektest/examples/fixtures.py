"""Fixture examples for snektest."""

from collections.abc import AsyncGenerator, Generator

from snektest import assert_eq, fixture, load_fixture, test


@fixture
def user_fixture() -> Generator[dict[str, str]]:
    """Create a fresh user for one test and tear it down afterward."""
    user: dict[str, str] = {"name": "Ada"}
    yield user
    user.clear()


@fixture(scope="session")
async def config_fixture() -> AsyncGenerator[dict[str, str]]:
    """Create shared configuration once for the whole test session."""
    config: dict[str, str] = {"environment": "test"}
    yield config
    config.clear()


@fixture(scope="run")
def service_descriptor() -> Generator[tuple[str, int]]:
    """Publish inert connection details from one command-owned resource."""
    yield ("127.0.0.1", 5432)


@test(mark="fast")
def test_function_fixture() -> None:
    """Function fixtures are loaded first and torn down for each test."""
    user = load_fixture(user_fixture())

    assert_eq(user["name"], "Ada")


@test(mark="fast")
async def test_session_fixture() -> None:
    """Async session fixtures can be awaited at the start of async tests."""
    config = await load_fixture(config_fixture())

    assert_eq(config["environment"], "test")


@test(mark="fast", mutex="example-service")
def test_run_fixture() -> None:
    """Run descriptors are copied into the process executing this test."""
    host, port = load_fixture(service_descriptor())

    assert_eq((host, port), ("127.0.0.1", 5432))
