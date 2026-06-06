"""Fixture examples for snektest."""

from snektest import AsyncSessionFixture, Fixture, assert_eq, load_fixture, test


def user_fixture() -> Fixture[dict[str, str]]:
    """Create a fresh user for one test and tear it down afterward."""
    user = {"name": "Ada"}
    yield user
    user.clear()


async def config_fixture() -> AsyncSessionFixture[dict[str, str]]:
    """Create shared configuration once for the whole test session."""
    config = {"environment": "test"}
    yield config
    config.clear()


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
