"""Public equality diagnostics for SQL strings and structured reports."""

from typing import override

from snektest import (
    AssertionFailure,
    Param,
    assert_eq,
    assert_in,
    assert_is,
    assert_lt,
    assert_raises,
    test,
)


@test(mark="fast")
def test_long_sql_mismatch_identifies_character_and_local_context() -> None:
    actual = "SELECT " + "column, " * 49 + "id FROM users"
    expected = "SELECT " + "column, " * 49 + "key FROM users"
    with assert_raises(AssertionFailure) as caught:
        assert_eq(actual, expected)

    message = str(caught.exception)
    assert_in("First difference at character 399 (0-based)", message)
    assert_in("id FROM users", message)
    assert_in("key FROM users", message)
    assert_lt(len(message), 2000)


@test(mark="fast")
def test_multiline_sql_has_expected_to_actual_unified_diff() -> None:
    with assert_raises(AssertionFailure) as caught:
        assert_eq(
            "SELECT id\nFROM users\nWHERE active = 0",
            "SELECT id\nFROM users\nWHERE active = 1",
        )

    assert_in(
        "--- expected\n+++ actual\n@@ -1,3 +1,3 @@\n SELECT id\n FROM users\n-WHERE active = 1\n+WHERE active = 0",
        str(caught.exception),
    )


@test(mark="fast")
def test_dictionary_reports_missing_extra_and_differing_keys() -> None:
    with assert_raises(AssertionFailure) as caught:
        assert_eq({"name": "alice", "extra": 7}, {"name": "bob", "missing": 8})

    message = str(caught.exception)
    assert_in("Missing keys (expected only): ['missing']", message)
    assert_in("Extra keys (actual only): ['extra']", message)
    assert_in("Differing keys: ['name']", message)
    assert_in("At $['name']: 'alice' != 'bob'", message)


@test(mark="fast")
def test_nested_typing_report_identifies_first_differing_sequence_index() -> None:
    with assert_raises(AssertionFailure) as caught:
        assert_eq(
            {"types": [{"name": "int"}, {"name": "str"}]},
            {"types": [{"name": "int"}, {"name": "bytes"}]},
        )

    message = str(caught.exception)
    assert_in("First differing index at $['types']: 1", message)
    assert_in("At $['types'][1]['name']: 'str' != 'bytes'", message)


@test(mark="fast")
def test_hostile_representation_cannot_replace_the_equality_failure() -> None:
    calls: list[str] = []

    class Hostile:
        @override
        def __ne__(self, other: object) -> bool:
            _ = other
            calls.append("ne")
            return True

        @override
        def __repr__(self) -> str:
            calls.append("repr")
            msg = "broken representation"
            raise RuntimeError(msg)

    with assert_raises(AssertionFailure) as caught:
        assert_eq(Hostile(), Hostile())

    assert_in("<Hostile>", str(caught.exception))
    assert_eq(calls, ["ne"])


@test(mark="fast")
def test_diagnostics_do_not_repeat_or_guess_custom_nested_equality() -> None:
    calls: list[object] = []

    class EqualToAnything:
        __hash__ = None

        @override
        def __eq__(self, other: object) -> bool:
            calls.append(other)
            return True

    with assert_raises(AssertionFailure) as caught:
        assert_eq([EqualToAnything(), 1], [0, 2])

    assert_eq(calls, [0])
    assert_in(
        "Details unavailable at $[0]: custom values are not inspected.",
        str(caught.exception),
    )


@test(mark="fast")
def test_cyclic_values_have_a_bounded_preview() -> None:
    actual: list[object] = [0]
    expected: list[object] = [1]
    actual.append(actual)
    expected.append(expected)
    with assert_raises(AssertionFailure) as caught:
        assert_eq(actual, expected)

    message = str(caught.exception)
    assert_in("<cycle>", message)
    assert_in("First differing index at $: 0", message)
    assert_lt(len(message), 8001)


@test(mark="fast")
def test_large_values_bound_output_and_explain_scan_limits() -> None:
    actual = list(range(10_000))
    expected = [*actual, -1]
    with assert_raises(AssertionFailure) as caught:
        assert_eq(actual, expected)

    message = str(caught.exception)
    assert_in("Comparison truncated", message)
    assert_lt(len(message), 8001)


@test(mark="fast")
def test_unknown_dictionary_keys_do_not_run_more_user_code() -> None:
    calls: list[str] = []

    class Key:
        @override
        def __hash__(self) -> int:
            return 0

        @override
        def __eq__(self, other: object) -> bool:
            _ = other
            calls.append("eq")
            return False

        @override
        def __repr__(self) -> str:
            calls.append("repr")
            return "unexpected"

    key = Key()
    with assert_raises(AssertionFailure) as caught:
        assert_eq({key: 1}, {key: 2})

    assert_eq(calls, [])
    assert_in(
        "custom or oversized dictionary keys are not inspected", str(caught.exception)
    )


@test(
    [
        Param("long-string", name="long-string"),
        Param("multiline", name="multiline"),
        Param("deep", name="deep"),
        Param("wide", name="wide"),
        Param("mapping", name="mapping"),
    ],
    mark="fast",
)
def test_explanation_limits_are_explicit_and_bounded(kind: str) -> None:
    actual: object
    expected: object
    if kind == "long-string":
        actual = "x" * 100_001 + "a"
        expected = "x" * 100_001 + "b"
    elif kind == "multiline":
        actual = "actual\n" * 1000
        expected = "expected\n" * 1000
    elif kind == "deep":
        actual = 0
        expected = 1
        for _ in range(12):
            actual = [actual]
            expected = [expected]
    elif kind == "wide":
        actual = [[0] * 50 for _ in range(50)]
        expected_rows = [[0] * 50 for _ in range(50)]
        expected_rows[-1][-1] = 1
        expected = expected_rows
    else:
        actual = dict.fromkeys(range(100), 0)
        expected = dict.fromkeys(range(100), 1)
    with assert_raises(AssertionFailure) as caught:
        assert_eq(actual, expected)

    assert_in("truncated", str(caught.exception))
    assert_lt(len(str(caught.exception)), 8001)


@test(mark="fast")
def test_tuple_and_list_length_mismatches_identify_the_missing_index() -> None:
    for actual, expected in (([1], [1, 2]), ((1, 2), (1,))):
        with assert_raises(AssertionFailure) as caught:
            assert_eq(actual, expected)
        assert_in(
            "First differing index at $: 1 (length mismatch", str(caught.exception)
        )


@test(mark="fast")
def test_newline_only_difference_is_not_lost_in_line_splitting() -> None:
    with assert_raises(AssertionFailure) as caught:
        assert_eq("value", "value\n")
    assert_in("First difference at character 5 (0-based)", str(caught.exception))


@test(mark="fast")
def test_passing_comparisons_do_not_inspect_values_and_custom_messages_stay_exact() -> (
    None
):
    calls: list[str] = []

    class Equal:
        @override
        def __ne__(self, other: object) -> bool:
            _ = other
            calls.append("ne")
            return False

        @override
        def __repr__(self) -> str:
            calls.append("repr")
            return "must not render"

    assert_eq(Equal(), Equal())
    assert_eq(calls, ["ne"])
    assert_eq(1, True)
    assert_eq([1], [1.0])
    actual = [1]
    expected = [2]
    with assert_raises(AssertionFailure) as caught:
        assert_eq(actual, expected, msg="Keep my message\nexactly.")
    assert_eq(str(caught.exception), "Keep my message\nexactly.")
    assert_is(caught.exception.actual, actual)
    assert_is(caught.exception.expected, expected)
    assert_eq(caught.exception.operator, "==")
    with assert_raises(AssertionFailure) as empty_message:
        assert_eq(1, 2, msg="")
    assert_eq(str(empty_message.exception), "1 != 2")


@test(
    [Param(ValueError, name="error"), Param(KeyboardInterrupt, name="interrupt")],
    mark="fast",
)
def test_original_comparison_errors_and_interruptions_propagate(
    error_type: type[BaseException],
) -> None:
    error = error_type()

    class BrokenComparison:
        @override
        def __ne__(self, other: object) -> bool:
            _ = other
            raise error

    with assert_raises(error_type) as caught:
        assert_eq(BrokenComparison(), None)
    assert_is(caught.exception, error)
