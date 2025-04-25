from snektest import test


@test()
def passes() -> None:
    assert True


def ignored() -> None:
    msg = "You should never see this error"
    raise ValueError(msg)


def test_ignored() -> None:
    msg = "You should never see this error"
    raise ValueError(msg)
