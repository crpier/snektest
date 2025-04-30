from snektest import load_params, test


def function_under_test(value: str) -> str:
    return value.replace(";", " ").strip()


def params() -> list[tuple[str, str]]:
    """Gather params.
    Returns:
        tuple[str, str]: A tuple of two strings: input and expected output.
    """
    return [
        (";hello world", "hello world"),
        ("hello;world", "hello world"),
        ("Hello world;", "Hello world"),
        ("hello world", "hello world"),
    ]


@test()
def test_passes() -> None:
    result, expected = load_params(params)
    assert function_under_test(result) == expected
