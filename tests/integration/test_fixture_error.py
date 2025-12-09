from collections.abc import Generator

from snektest import load_fixture, session_fixture, test


def good_fixture() -> Generator[None]:
    yield None


def fixture_with_failing_teardown() -> Generator[None]:
    yield None
    msg = "failing teardown"
    raise ValueError(msg)


@session_fixture()
def session_fixture_with_failing_teardown() -> Generator[None]:
    yield None
    msg = "failing teardown"
    raise ValueError(msg)


@test()
def test_with_good_fixture() -> None:
    _ = load_fixture(good_fixture())


@test()
def test_with_bad_fixture() -> None:
    _ = load_fixture(fixture_with_failing_teardown())


@test()
def test_with_bad_session_fixture() -> None:
    _ = load_fixture(session_fixture_with_failing_teardown())
