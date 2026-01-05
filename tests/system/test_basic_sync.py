from collections.abc import Generator

from snektest import load_fixture, session_fixture, test
from snektest.models import Param


@session_fixture()
def fixture_for_session() -> Generator[int]:
    print("session fixture started")
    yield 10
    print("session fixture ended")


def simple_fixture_lol() -> Generator[None]:
    print("simple fixture started")
    yield
    print("simple fixture ended")


@test()
async def test_with_session_fixture() -> None:
    print("test started")
    session_fixture_result = load_fixture(fixture_for_session())
    load_fixture(simple_fixture_lol())
    assert session_fixture_result == 10
    print("test ends")


@test()
def another_test_with_session_fixture() -> None:
    session_fixture_result = load_fixture(fixture_for_session())
    assert session_fixture_result == 10


@test()
def test_no_params() -> None:
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
def test_1_params(param1: str) -> None:
    assert param1.strip() == "bab"


@test(first_param_set, second_param_set)
def test_2_params(param1: str, param2: int) -> None:
    assert param1.strip() == "bab"
    assert param2 == 5


def simple_fixture() -> Generator[str]:
    yield "some fixture"


def fixture_with_param(param1: str) -> Generator[str]:
    yield param1


def fixture_with_teardown() -> Generator[str]:
    yield "some fixture"


def fixture_with_teardown_and_param(param: str) -> Generator[str]:
    yield param


@test()
def test_with_simple_fixture() -> None:
    fixture = load_fixture(simple_fixture())
    assert fixture == "some fixture"


@test([Param("the number", "single-param"), Param("the number2", "single-param-2")])
def test_with_param_fixture(param1: str) -> None:
    _ = load_fixture(fixture_with_param(param1))
    assert True


@test([Param("the number", "single-param")])
def test_with_param_and_teardown_fixture(param1: str) -> None:
    fixture_result = load_fixture(fixture_with_teardown_and_param(param1))
    assert fixture_result == "the number"
