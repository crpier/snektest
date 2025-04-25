from snektest.runner import fixture, test


@fixture
def fixture_func() -> None:
    pass


@test()
def test_basic_works() -> None:
    assert True


@test()
def test_basic_fails() -> None:
    assert True


# TODO: test xfail, xpass, async tests, fixtures, async fixtures
