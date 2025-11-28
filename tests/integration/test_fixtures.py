from collections.abc import AsyncGenerator

from snektest import load_fixture, session_fixture, test


@session_fixture()
async def basic_fixture_1() -> AsyncGenerator[None]:
    print("basic fixture1 starts")
    yield
    print("basic fixture1 ends")


@session_fixture()
async def basic_fixture_2() -> AsyncGenerator[None]:
    print("basic fixture2 starts")
    yield
    print("basic fixture2 ends")


@test()
async def test_with_2_fixtures() -> None:
    await load_fixture(basic_fixture_1())
    await load_fixture(basic_fixture_2())
