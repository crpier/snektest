"""Keep documentation code blocks self-verifying.

Every ```python block in ``README.md`` and ``snektest/agent_docs.py`` is
type-checked under this repo's strict pyright config and executed with
snektest; any adjacent ```text block is diffed against the captured output.
Blocks opt out or flip expectations with ``snektest-doc`` directives (see
:mod:`testutils.docblocks`).

This turns documentation drift into a test failure instead of an audit
finding, and enforces the "never hand-write sample output" rule in AGENTS.md.
"""

from snektest import Param, assert_in, assert_ne, fail, test
from snektest.assertions import assert_eq
from testutils.doc_verify import normalize_output, run_block, typecheck_blocks
from testutils.docblocks import CodeBlock, doc_python_blocks

_BLOCKS = doc_python_blocks()
_RUNNABLE = [block for block in _BLOCKS if "skip-run" not in block.directives]


@test(mark="slow")
def test_doc_blocks_typecheck() -> None:
    """Every documented block matches its pyright expectation in one run."""
    blocks = [b for b in _BLOCKS if "skip-typecheck" not in b.directives]
    results = typecheck_blocks(blocks)

    problems: list[str] = []
    for block in blocks:
        result = results[block.slug]
        expects_error = "expect-type-error" in block.directives
        where = f"{block.slug} ({block.source}:{block.line})"
        if expects_error and result.error_count == 0:
            msg = f"{where} is marked expect-type-error but pyright found none."
            problems.append(msg)
        elif not expects_error and result.error_count:
            joined = "\n  ".join(result.messages)
            problems.append(f"{where} failed pyright:\n  {joined}")

    if problems:
        fail("\n\n".join(problems))


@test(
    [Param(value=block, name=block.slug) for block in _RUNNABLE],
    mark="slow",
)
def test_doc_block_runs(block: CodeBlock) -> None:
    """Each runnable block executes as documented; output blocks match."""
    proc = run_block(block)
    where = f"{block.slug} ({block.source}:{block.line})"

    if "expect-fail" in block.directives:
        assert_ne(
            proc.returncode,
            0,
            msg=f"{where} is marked expect-fail but exited 0:\n{proc.stdout}",
        )
    else:
        assert_eq(
            proc.returncode,
            0,
            msg=f"{where} failed unexpectedly:\n{proc.stdout}\n{proc.stderr}",
        )

    if block.following_text is not None:
        expected = normalize_output(block.following_text).strip()
        captured = normalize_output(proc.stdout)
        assert_in(
            expected,
            captured,
            msg=(
                f"{where}: documented ```text output not found in captured "
                f"output.\n--- documented ---\n{expected}\n"
                f"--- captured ---\n{captured}"
            ),
        )
