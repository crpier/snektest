"""Unit tests for snektest-doc directive parsing (no pyright spawn).

The parser is private (``_parse_directives``); it is exercised through the
public :func:`extract_blocks` entry point that drives it.
"""

from snektest import test
from snektest.assertions import assert_eq, assert_raises
from testutils.docblocks import CodeBlock, ExpectedDiagnostic, extract_blocks


def _block_with(directive: str) -> CodeBlock:
    """Extract a single python block carrying ``directive``."""
    text = f"<!-- snektest-doc: {directive} -->\n```python\nx = 1\ny = 2\n```\n"
    blocks = extract_blocks(text, "doc")
    assert_eq(len(blocks), 1)
    return blocks[0]


@test()
def test_bare_flag_directive() -> None:
    """A bare flag lands in the directive set with no expectation."""
    block = _block_with("expect-type-error")
    assert_eq(block.directives, frozenset({"expect-type-error"}))
    assert_eq(block.expected_diagnostics, ())


@test()
def test_pinned_rule_only() -> None:
    """``=rule`` routes to expected_diagnostics with line None, not the flags."""
    block = _block_with("expect-type-error=reportArgumentType")
    assert_eq(block.directives, frozenset())
    assert_eq(
        block.expected_diagnostics,
        (ExpectedDiagnostic(rule="reportArgumentType", line=None),),
    )


@test()
def test_pinned_rule_and_line() -> None:
    """``=rule@line`` captures a 1-based line."""
    block = _block_with("expect-type-error=reportArgumentType@3")
    assert_eq(
        block.expected_diagnostics,
        (ExpectedDiagnostic(rule="reportArgumentType", line=3),),
    )


@test()
def test_mixed_flag_and_pinned() -> None:
    """A flag and a pinned expectation can coexist in one comment."""
    block = _block_with("skip-run, expect-type-error=reportCallIssue@5")
    assert_eq(block.directives, frozenset({"skip-run"}))
    assert_eq(
        block.expected_diagnostics,
        (ExpectedDiagnostic(rule="reportCallIssue", line=5),),
    )


@test()
def test_empty_rule_rejected() -> None:
    """``expect-type-error=`` with no rule is an error."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("expect-type-error=")
    assert_eq("requires a rule name" in str(exc.exception), True)


@test()
def test_non_integer_line_rejected() -> None:
    """A non-integer line component is an error."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("expect-type-error=reportArgumentType@x")
    assert_eq("must be an integer" in str(exc.exception), True)


@test()
def test_zero_line_rejected() -> None:
    """Lines are 1-based; 0 is rejected."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("expect-type-error=reportArgumentType@0")
    assert_eq("must be >= 1" in str(exc.exception), True)


@test()
def test_skip_typecheck_with_pin_rejected() -> None:
    """A pinned expectation under skip-typecheck would never run, so it raises."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("skip-typecheck, expect-type-error=reportCallIssue@5")
    assert_eq("skip-typecheck cannot be combined" in str(exc.exception), True)


@test()
def test_skip_typecheck_with_bare_flag_rejected() -> None:
    """The bare expect-type-error flag is equally moot under skip-typecheck."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("skip-typecheck, expect-type-error")
    assert_eq("skip-typecheck cannot be combined" in str(exc.exception), True)


@test()
def test_unknown_directive_rejected() -> None:
    """An unrelated unknown token still raises."""
    with assert_raises(ValueError) as exc:
        _ = _block_with("not-a-real-directive")
    assert_eq("Unknown snektest-doc directive" in str(exc.exception), True)
