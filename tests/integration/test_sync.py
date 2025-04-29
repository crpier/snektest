from collections.abc import Generator

from snektest import load_fixture, test

fixture_without_teardown_started_up = False


@test()
def passes() -> None:
    assert True


def ignored() -> None:
    msg = "You should never see this error"
    raise ValueError(msg)


def test_ignored() -> None:
    msg = "You should never see this error"
    raise ValueError(msg)


def fixture_without_teardown() -> int:
    global fixture_without_teardown_started_up  # noqa: PLW0603
    fixture_without_teardown_started_up = True
    return 1


@test()
def test_fixture_without_teardown() -> None:
    fixture_value = load_fixture(fixture_without_teardown)
    assert fixture_value == 1
    assert fixture_without_teardown_started_up is True


fixture_with_teardown_started_up = False
fixture_with_teardown_torn_down = False


def fixture_with_teardown() -> Generator[int]:
    global fixture_with_teardown_started_up  # noqa: PLW0603
    fixture_with_teardown_started_up = True
    yield 1
    global fixture_with_teardown_torn_down  # noqa: PLW0603
    fixture_with_teardown_torn_down = True


@test()
def test_fixture_with_teardown() -> None:
    fixture_value = load_fixture(fixture_with_teardown)
    assert fixture_value == 1
    assert fixture_with_teardown_started_up is True


fixture_incrementee = 0


def fixture_that_increments() -> int:
    global fixture_incrementee  # noqa: PLW0603
    fixture_incrementee += 1
    return 1


@test()
def test_fixture_is_called_only_once() -> None:
    _ = load_fixture(fixture_that_increments)
    increment_count = load_fixture(fixture_that_increments)
    assert increment_count == 1
    assert fixture_incrementee == 1


# TODO: how to test that a fixture is called multiple times in different tests?
