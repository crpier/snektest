"""Python-native type contracts; install snektest[typing] before running."""

lazy from typing import assert_type

lazy from snektest import assert_type_error, test_typing


def takes_int(value: int) -> int:
    return value


@test_typing
def test_argument_types(symbolic: int) -> None:
    """Parameters are symbolic types, not runtime fixture inputs."""
    assert_type(takes_int(symbolic), int)
    with assert_type_error("invalid-argument-type"):
        takes_int("wrong")


async def length(value: str) -> int:
    return len(value)


@test_typing
async def test_async_argument_types() -> None:
    """Local imports and await expressions are checked without executing them."""
    from snektest import assert_type_error as reject  # noqa: PLC0415 -- demonstrate a static local import

    assert_type(await length("hello"), int)
    with reject("invalid-argument-type"):
        await length(42)
