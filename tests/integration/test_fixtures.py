from collections.abc import AsyncGenerator

from snektest import session_fixture


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
