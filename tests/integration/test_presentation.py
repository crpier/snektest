from snektest import test
from snektest.assertions import assert_eq


@test()
def test_list_comparison() -> None:
    assert_eq(["pula"], ["pizda"])


@test()
def test_nested_list_comparison() -> None:
    assert_eq(
        [1, 2, [3, 4, "foo"]],
        [1, 2, [3, 4, "bar"]],
    )


@test()
def test_dict_comparison() -> None:
    assert_eq(
        {"name": "alice", "age": 30},
        {"name": "bob", "age": 30},
    )


@test()
def test_multiline_string_comparison() -> None:
    assert_eq(
        "hello\nworld\nfoo",
        "hello\nworld\nbar",
    )
