from collections.abc import Generator

from snektest import fixture, load_fixture, test

root_fixture_started_up = False


def passes() -> None:
    assert True


@fixture()
def fixture_without_teardown() -> int:
    return 1


@test()
def fixture_without_teardown_passes() -> None:
    fixture_value = load_fixture(fixture_without_teardown)
    assert fixture_value == 1


@fixture()
def load_root_fixture() -> Generator[int]:
    global root_fixture_started_up  # noqa: PLW0603
    root_fixture_started_up = True

    yield 1

    global root_fixture_torn_down  # noqa: PLW0603
    root_fixture_torn_down = True


@fixture()
def load_child_fixture() -> Generator[int]:
    root_fixture = load_fixture(load_root_fixture)
    global child_fixture_started_up  # noqa: PLW0603
    child_fixture_started_up = True

    yield root_fixture + 1

    global child_fixture_torn_down  # noqa: PLW0603
    child_fixture_torn_down = True


@test()
def root_fixture_passes_correct_value() -> None:
    root_fixture = load_fixture(load_root_fixture)
    assert root_fixture == 1


@test()
def root_fixture_is_started_up() -> None:
    result = load_fixture(load_root_fixture)
    assert root_fixture_started_up is True
    assert result == 1


@test()
def root_fixture_is_torn_down() -> None:
    assert root_fixture_torn_down is True


@test()
def child_fixture_passes_correct_value() -> None:
    child_fixture = load_fixture(load_child_fixture)
    assert child_fixture == 2


@test()
def child_fixture_is_started_up() -> None:
    result = load_fixture(load_child_fixture)
    assert child_fixture_started_up is True
    assert result == 2


@test()
def child_fixture_is_torn_down() -> None:
    result = load_fixture(load_child_fixture)
    assert child_fixture_torn_down is True
    assert result == 2


@fixture()
def sample_fixture() -> Generator[str]:
    value = "fixture value"
    yield value


@test(1, 2)
@test(2, 3)
@test(3, 4)
def simple_params(a: int, b: int) -> None:
    assert a + b == a + b


@test([1, 2, 3], 7)
@test([2, 3, 4], 10)
def params_and_fixtures(lst: list[int], expected_sum: int) -> None:
    root_value = load_fixture(load_root_fixture)
    assert sum(lst) + root_value == expected_sum


@test()
def with_fixture() -> None:
    fixture_value = load_fixture(sample_fixture)
    assert fixture_value == "fixture value", (
        f"Expected 'fixture value', but got '{fixture_value}'"
    )


@fixture(1, 4, scope="test")
@fixture(1, 2, scope="test")
@fixture(1, 0, scope="test")
def my_parametrized_fixture(a: int, b: int) -> Generator[int]:
    yield a + b


@fixture(6, scope="test")
@fixture(4, scope="test")
@fixture(2, scope="test")
def second_parametrized_fixture(multiplier: int) -> Generator[int]:
    yield multiplier


@test()
def with_parametrized_fixture() -> None:
    result = load_fixture(my_parametrized_fixture)
    assert result >= 1 or result // 2 != 0


@test()
def with_2_parametrized_fixtures() -> None:
    result = load_fixture(my_parametrized_fixture)
    result_2 = load_fixture(second_parametrized_fixture)
    assert result != result_2


@test(3)
@test(2)
@test(1)
def with_2_parametrized_fixtures_and_parametrized_test(test_param: int) -> None:
    result = load_fixture(my_parametrized_fixture)
    result_2 = load_fixture(second_parametrized_fixture)
    assert test_param - 3 < result + result_2
