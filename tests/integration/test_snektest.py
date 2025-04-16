from snektest.runner import fixture, test


@fixture
def fixture_func():
    print("I'm a fixture")


@test()
def test_basic_works():
    assert True


@test()
def test_basic_fails():
    assert True


# TODO: test xfail, xpass, async tests, fixtures, async fixtures
