"""Keyword grammar and command-line validation."""

from snektest import Param, assert_eq, assert_isinstance, assert_raises, test
from snektest.cli import CliOptions, ParseError, parse_cli_args
from snektest.keywords import KeywordExpression
from snektest.models import BadRequestError


@test(
    [
        Param(value=("", True), name="empty"),
        Param(value=(" \t", True), name="whitespace"),
        Param(value=("\nalpha", True), name="leading-whitespace"),
        Param(value=("\n", True), name="blank-expression"),
        Param(value=("ALPHA", True), name="case-insensitive"),
        Param(value=("alp", True), name="substring"),
        Param(value=("missing", False), name="absent"),
        Param(value=("alpha or missing and absent", True), name="and-precedence"),
        Param(value=("(alpha or missing) and absent", False), name="grouping"),
        Param(value=("not missing and alpha", True), name="not-precedence"),
        Param(value=("not (alpha or missing)", False), name="negated-group"),
        Param(value=("not not alpha", True), name="double-negation"),
        Param(value=("alpha and not absent", True), name="exclusion"),
        Param(value=("AND", False), name="uppercase-is-name"),
        Param(value=("fast", True), name="marker"),
        Param(value=("[red]", True), name="bracketed-case"),
        Param(value=("café", True), name="unicode"),
        Param(value=("test_file.py", True), name="module"),
        Param(value=("alpha[red]", True), name="function-case"),
        Param(value=("pyalpha", False), name="component-boundary"),
        Param(value=("one/two", True), name="slash"),
        Param(value=("a+b:c-d", True), name="punctuation"),
    ],
    mark="fast",
)
def test_expression_matches_names(example: tuple[str, bool]) -> None:
    expression, expected = example

    actual = KeywordExpression(expression).matches(
        ("test_file.py", "test_alpha[red]", "fast", "café", "one/two", "a+b:c-d")
    )

    assert_eq(actual, expected)


@test(
    [
        Param(value=value, name=name)
        for name, value in (
            ("trailing-and", "alpha and"),
            ("leading-or", "or alpha"),
            ("bare-not", "not"),
            ("adjacent-names", "alpha beta"),
            ("empty-group", "()"),
            ("missing-close", "(alpha"),
            ("extra-close", "alpha)"),
            ("operator-before-close", "(alpha or)"),
            ("quoted-name", "'alpha'"),
            ("regex", "alpha.*"),
            ("newline", "alpha\nbeta"),
            ("call", "alpha(beta)"),
            ("keyword-argument", "alpha(x=1)"),
            ("comma", "alpha,beta"),
        )
    ],
    mark="fast",
)
def test_invalid_expression_is_request_error(expression: str) -> None:
    with assert_raises(BadRequestError):
        KeywordExpression(expression)


@test(mark="fast")
def test_deep_expression_does_not_recurse() -> None:
    expression = "(" * 2000 + "not " * 2000 + "alpha" + ")" * 2000

    assert_eq(KeywordExpression(expression).matches(("alpha",)), True)


@test(
    [Param(value="-k", name="short"), Param(value="--keyword", name="long")],
    mark="fast",
)
def test_cli_accepts_keyword(flag: str) -> None:
    options = assert_isinstance(
        parse_cli_args([flag, "alpha and not beta"]), CliOptions
    )

    assert_eq(options.keyword, "alpha and not beta")


@test(
    [
        Param(value=["-k"], name="missing-value"),
        Param(value=["-k", "--collect-only"], name="flag-as-value"),
        Param(value=["-k", "alpha and"], name="invalid-expression"),
        Param(value=["-k", "alpha", "--keyword", "beta"], name="repeated"),
        Param(value=["-k", "", "-k", "alpha"], name="repeated-empty"),
        Param(value=["--help", "-k", "alpha"], name="informational"),
    ],
    mark="fast",
)
def test_cli_rejects_invalid_keyword_arguments(arguments: list[str]) -> None:
    _ = assert_isinstance(parse_cli_args(arguments), ParseError)
