from snek.snektest.runner import fixture, load_fixture, test

root_fixture_started_up = False
root_fixture_torn_down = False
child_fixture_started_up = False
child_fixture_torn_down = False

# TODO: also test that fixture setup and teardowns are called only once


@fixture()
def load_root_fixture():
    global root_fixture_started_up
    root_fixture_started_up = True

    yield 1

    global root_fixture_torn_down
    root_fixture_torn_down = True


@fixture()
def load_child_fixture():
    root_fixture = load_fixture(load_root_fixture)
    global child_fixture_started_up
    child_fixture_started_up = True

    yield root_fixture + 1

    global child_fixture_torn_down
    child_fixture_torn_down = True


@test()
def root_fixture_passes_correct_value():
    root_fixture = load_fixture(load_root_fixture)
    assert root_fixture == 1


@test()
def root_fixture_is_started_up():
    load_fixture(load_root_fixture)
    assert root_fixture_started_up is True


# TODO: this test is not working correctly when ran on its own lol
@test()
def root_fixture_is_torn_down():
    assert root_fixture_torn_down is True


@test()
def child_fixture_passes_correct_value():
    child_fixture = load_fixture(load_child_fixture)
    assert child_fixture == 2


@test()
def child_fixture_is_started_up():
    load_fixture(load_child_fixture)
    assert child_fixture_started_up is True


@test()
def child_fixture_is_torn_down():
    load_fixture(load_child_fixture)
    assert child_fixture_torn_down is True


@fixture()
def sample_fixture():
    value = "fixture value"
    yield value


@test(1, 2)
@test(2, 3)
@test(3, 4)
def test_simple_params(a: int, b: int):
    assert a + b == a + b


@test([1, 2, 3], 7)
@test([2, 3, 4], 10)
def test_params_and_fixtures(lst: list[int], expected_sum: int):
    root_value = load_fixture(load_root_fixture)
    assert sum(lst) + root_value == expected_sum


@test()
def test_with_fixture():
    fixture_value = load_fixture(sample_fixture)
    assert (
        fixture_value == "fixture value"
    ), f"Expected 'fixture value', but got '{fixture_value}'"


@fixture(1, 4, scope="test")
@fixture(1, 2, scope="test")
@fixture(1, 0, scope="test")
def my_parametrized_fixture(a: int, b: int):
    yield a + b


@fixture(6, scope="test")
@fixture(4, scope="test")
@fixture(2, scope="test")
def second_parametrized_fixture(multiplier: int):
    yield multiplier


@test()
def test_with_parametrized_fixture():
    result = load_fixture(my_parametrized_fixture)
    assert result >= 1 or result // 2 != 0


@test()
def test_with_2_parametrized_fixtures():
    result = load_fixture(my_parametrized_fixture)
    result_2 = load_fixture(second_parametrized_fixture)
    assert result != result_2


@test(3)
@test(2)
@test(1)
def test_with_2_parametrized_fixtures_and_parametrized_test(test_param: int):
    result = load_fixture(my_parametrized_fixture)
    result_2 = load_fixture(second_parametrized_fixture)
    assert test_param - 3 < result + result_2
