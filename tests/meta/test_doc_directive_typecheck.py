"""End-to-end checks for pinned type-error directives (spawns pyright).

These build synthetic blocks rather than reading the real docs, so they assert
the precise-diagnostic machinery directly: 1-based line mapping, rule capture,
and the pass/fail logic in :func:`check_block_diagnostics`.
"""

from snektest import assert_eq, assert_true, test
from testutils.doc_verify import check_block_diagnostics, typecheck_blocks
from testutils.docblocks import CodeBlock, ExpectedDiagnostic

# A type error on block line 2; pyright reports ``reportAssignmentType``.
_BAD_CODE = "a = 1\nx: int = 'hello'\n"


def _block(*, index: int, expected: tuple[ExpectedDiagnostic, ...] = ()) -> CodeBlock:
    return CodeBlock(
        source="synthetic",
        lang="python",
        code=_BAD_CODE,
        line=1,
        index=index,
        expected_diagnostics=expected,
    )


@test(mark="slow")
def test_typecheck_reports_line_and_rule() -> None:
    """typecheck_blocks maps pyright's 0-based line to a 1-based block line."""
    block = _block(index=0)
    result = typecheck_blocks([block])[block.slug]

    assert_eq(result.error_count, 1)
    diag = result.diagnostics[0]
    assert_eq(diag.rule, "reportAssignmentType")
    assert_eq(diag.line, 2)


@test(mark="slow")
def test_correct_pin_passes() -> None:
    """A pin matching the reported rule and line yields no problems."""
    block = _block(
        index=0,
        expected=(ExpectedDiagnostic(rule="reportAssignmentType", line=2),),
    )
    result = typecheck_blocks([block])[block.slug]
    assert_eq(check_block_diagnostics(block, result), [])


@test(mark="slow")
def test_wrong_line_fails_naming_what_was_found() -> None:
    """A pin with the wrong line fails and names the diagnostic found."""
    block = _block(
        index=0,
        expected=(ExpectedDiagnostic(rule="reportAssignmentType", line=1),),
    )
    result = typecheck_blocks([block])[block.slug]
    problems = check_block_diagnostics(block, result)

    assert_eq(len(problems), 1)
    assert_true("reportAssignmentType@1" in problems[0])
    assert_true("reportAssignmentType@2" in problems[0])


@test(mark="slow")
def test_wrong_rule_fails() -> None:
    """A pin with the wrong rule fails."""
    block = _block(
        index=0,
        expected=(ExpectedDiagnostic(rule="reportArgumentType", line=2),),
    )
    result = typecheck_blocks([block])[block.slug]
    problems = check_block_diagnostics(block, result)
    assert_eq(len(problems), 1)
    assert_true("reportArgumentType@2" in problems[0])
