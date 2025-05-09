from snektest import Param, load_params, test


def function_under_test(value: str) -> str:
    return value.replace(";", " ").strip()


def params() -> list[Param[tuple[str, str]]]:
    """Gather params.
    Returns:
        tuple[str, str]: A tuple of two strings: input and expected output.
    """
    return [
        Param((";hello world", "hello world"), name="semicolon_at_start"),
        Param(("hello;world", "hello world"), name="semicolon_at_middle"),
        Param(("Hello world;", "Hello world"), name="semicolon_at_end"),
        Param(("hello world", "hello world"), name="no_semicolon"),
    ]


@test()
def test_passes() -> None:
    result, expected = load_params(params)
    assert function_under_test(result) == expected
