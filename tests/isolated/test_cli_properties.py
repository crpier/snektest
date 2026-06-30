"""Property-based tests for the pure CLI argument parser.

`parse_cli_args` is a pure `list[str] -> CliOptions | ParseError` function, which
makes it a good property-testing target: across arbitrary argv it must stay
total (never raise), deterministic, and only ever produce a `CliOptions` whose
fields satisfy the parser's own invariants.
"""

from __future__ import annotations

from hypothesis import settings
from hypothesis import strategies as st

from snektest import assert_eq, assert_in, assert_isinstance, assert_true
from snektest.cli import VALID_MARKER_VALUES, CliOptions, ParseError, parse_cli_args
from snektest.decorators import test_hypothesis

# Tokens the parser treats specially, mixed with free text so generated argv
# exercises both the valid and the error paths.
_KNOWN_TOKENS = [
    "-h",
    "--help",
    "--agent-docs",
    "--llms",
    "--examples",
    "examples",
    "--example",
    "example",
    "-s",
    "--json-output",
    "--pdb",
    "--mark",
    "--timeout",
    "fast",
    "medium",
    "slow",
    "1.5",
    "0",
    "-bogus",
    ".",
    "tests/test_x.py",
    "tests/test_x.py::case",
]

_argv = st.lists(st.one_of(st.sampled_from(_KNOWN_TOKENS), st.text(max_size=8)))

# "Plain" tokens: never start with `-` and are never action words, so an argv
# built only from these must parse to pure filters.
_plain_tokens = st.text(min_size=1, max_size=8).filter(
    lambda s: not s.startswith("-") and s not in {"example", "examples"}
)


@settings(deadline=None)
@test_hypothesis(_argv, mark="fast")
def test_parser_is_total_and_deterministic(argv: list[str]) -> None:
    """Parsing never raises (reaching the body proves it) and is pure in argv."""
    result = parse_cli_args(argv)
    assert_eq(result, parse_cli_args(list(argv)))


@settings(deadline=None)
@test_hypothesis(_argv, mark="fast")
def test_successful_parse_satisfies_invariants(argv: list[str]) -> None:
    """Any returned CliOptions obeys the parser's documented invariants."""
    result = parse_cli_args(argv)
    if isinstance(result, ParseError):
        return

    # An action and test filters never coexist on success (it's a ParseError);
    # so a successful action run carries no filters.
    if result.action is not None:
        assert_eq(result.filters, ())
    else:
        # No action means at least one filter, defaulting to ".".
        assert_true(len(result.filters) >= 1)

    assert_true(result.mark is None or result.mark in VALID_MARKER_VALUES)
    assert_true(result.timeout is None or result.timeout > 0)


@settings(deadline=None)
@test_hypothesis(st.lists(_plain_tokens, min_size=1), mark="fast")
def test_plain_tokens_become_filters_in_order(tokens: list[str]) -> None:
    """Argv of only non-flag, non-action words parses to those filters verbatim."""
    result = parse_cli_args(tokens)
    result = assert_isinstance(result, CliOptions)
    assert_eq(result.action, None)
    assert_eq(result.filters, tuple(tokens))


@settings(deadline=None)
@test_hypothesis(st.sampled_from(sorted(VALID_MARKER_VALUES)), mark="fast")
def test_valid_mark_round_trips(mark: str) -> None:
    """`--mark <valid>` is preserved on the parsed options."""
    result = parse_cli_args(["--mark", mark, "."])
    result = assert_isinstance(result, CliOptions)
    assert_eq(result.mark, mark)


@settings(deadline=None)
@test_hypothesis(_plain_tokens, mark="fast")
def test_repeated_mark_is_rejected(value: str) -> None:
    """Two `--mark` flags are always a usage error, whatever the values."""
    result = parse_cli_args(["--mark", "fast", "--mark", "slow", value])
    result = assert_isinstance(result, ParseError)
    assert_in("--mark", result.message)
