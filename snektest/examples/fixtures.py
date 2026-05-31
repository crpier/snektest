"""Fixture examples for snektest."""

from collections.abc import AsyncGenerator, Generator

from snektest import assert_eq, load_fixture, session_fixture, test


def user_fixture() -> Generator[dict[str, str]]:
    """Create a fresh user for one test and tear it down afterward."""
    user = {"name": "Ada"}
    yield user
    user.clear()


@session_fixture()
async def config_fixture() -> AsyncGenerator[dict[str, str]]:
    """Create shared configuration once for the whole test session."""
    config = {"environment": "test"}
    yield config
    config.clear()


@test()
def test_function_fixture() -> None:
    """Function fixtures are loaded and torn down for each test."""
    user = load_fixture(user_fixture())
    assert_eq(user["name"], "Ada")


@test()
async def test_session_fixture() -> None:
    """Async session fixtures can be awaited from async tests."""
    config = await load_fixture(config_fixture())
    assert_eq(config["environment"], "test")
