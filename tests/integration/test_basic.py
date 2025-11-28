from collections.abc import AsyncGenerator

from snektest import load_fixture, session_fixture, test
from snektest.models import Param


@session_fixture()
async def fixture_for_session() -> AsyncGenerator[int]:
    yield 10


@test()
async def test_with_session_fixture() -> None:
    session_fixture_result = await load_fixture(fixture_for_session())
    assert session_fixture_result == 10


@test()
async def another_test_with_session_fixture() -> None:
    session_fixture_result = await load_fixture(fixture_for_session())
    assert session_fixture_result == 10


@test()
async def test_no_params() -> None:
    assert True


first_param_set = [
    Param(" bab ", "spaces both sides"),
    Param(" bab", "space left side"),
    Param("bab ", "space right side"),
    Param("bab", "no, spaces"),
]
second_param_set = [
    Param(5, "1 attempt"),
    Param(5, "2 attempts"),
    Param(5, "3 attempts"),
]


@test(first_param_set)
async def test_1_params(param1: str) -> None:
    assert param1.strip() == "bab"


@test(first_param_set, second_param_set)
async def test_2_params(param1: str, param2: int) -> None:
    assert param1.strip() == "bab"
    assert param2 == 5


async def simple_fixture() -> AsyncGenerator[str]:
    yield "some fixture"


async def fixture_with_param(param1: str) -> AsyncGenerator[str]:
    yield param1


async def fixture_with_teardown() -> AsyncGenerator[str]:
    yield "some fixture"


async def fixture_with_teardown_and_param(param: str) -> AsyncGenerator[str]:
    yield param


@test()
async def test_with_simple_fixture() -> None:
    fixture = await load_fixture(simple_fixture())
    assert fixture == "some fixture"


@test([Param("the number", "single-param"), Param("the number2", "single-param-2")])
async def test_with_param_fixture(param1: str) -> None:
    _ = await load_fixture(fixture_with_param(param1))
    assert True


@test([Param("the number", "single-param")])
async def test_with_param_and_teardown_fixture(param1: str) -> None:
    fixture_result = await load_fixture(fixture_with_teardown_and_param(param1))
    assert fixture_result == "the number"
