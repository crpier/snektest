"""Extract fenced code blocks and their directives from doc surfaces.

Used by the meta test that keeps documentation code blocks self-verifying:
every ```python block in the docs is type-checked under this repo's pyright
config and executed with snektest, and any adjacent ```text block is diffed
against the captured output.

Blocks can be annotated with an HTML comment directive on a line preceding the
opening fence (blank lines between are allowed)::

    <!-- snektest-doc: expect-fail -->
    <!-- snektest-doc: expect-type-error, skip-run -->

Recognized directives:

- ``skip-run``         do not execute the block with snektest
- ``skip-typecheck``   do not run pyright over the block
- ``expect-fail``      executing the block must fail (a test fails / errors)
- ``expect-type-error`` pyright must report at least one error for the block

``expect-type-error`` also takes an optional argument that pins a *specific*
diagnostic instead of merely "≥1 error somewhere"::

    expect-type-error=reportArgumentType      # ≥1 error with this rule
    expect-type-error=reportCallIssue@4        # that rule at block line 4

Block lines are counted from 1 at the line *after* the opening fence. These
pinned expectations are collected into ``CodeBlock.expected_diagnostics`` rather
than the ``directives`` flag set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from snektest.agent_docs import AGENT_DOCS

DIRECTIVE_RE = re.compile(r"<!--\s*snektest-doc:\s*(?P<body>.*?)\s*-->")
FENCE_RE = re.compile(r"^(?P<indent>\s*)```(?P<lang>[\w+-]*)\s*$")

VALID_DIRECTIVES = frozenset(
    {"skip-run", "skip-typecheck", "expect-fail", "expect-type-error"}
)


@dataclass(frozen=True)
class ExpectedDiagnostic:
    """A pyright diagnostic a block asserts pyright must report."""

    rule: str
    """Pyright rule name, e.g. ``reportArgumentType``."""
    line: int | None
    """1-based line within the block, or ``None`` to match the rule anywhere."""

    def __str__(self) -> str:
        return self.rule if self.line is None else f"{self.rule}@{self.line}"


@dataclass(frozen=True)
class CodeBlock:
    """A fenced code block extracted from a documentation surface."""

    source: str
    """Human-readable origin, e.g. ``README.md`` or ``agent_docs.py``."""
    lang: str
    """Fence language tag, e.g. ``python``, ``text``, ``bash``."""
    code: str
    """The block's contents (without the fences)."""
    line: int
    """1-based line number of the opening fence within the source."""
    index: int
    """0-based position of this block among all blocks from the source."""
    directives: frozenset[str] = frozenset()
    """Parsed ``snektest-doc`` bare-flag directives attached to the block."""
    expected_diagnostics: tuple[ExpectedDiagnostic, ...] = ()
    """Pinned ``expect-type-error=rule[@line]`` expectations for the block."""
    following_text: str | None = None
    """Contents of the immediately-following ```text block, if any."""

    @property
    def slug(self) -> str:
        """Stable identifier, e.g. ``README.md:block-03``."""
        return f"{self.source}:block-{self.index:02d}"


def _parse_expected_diagnostic(arg: str) -> ExpectedDiagnostic:
    """Parse the ``rule[@line]`` payload of an ``expect-type-error=`` token."""
    rule, sep, line_part = arg.partition("@")
    rule = rule.strip()
    if not rule:
        msg = (
            "expect-type-error= requires a rule name, "
            "e.g. expect-type-error=reportArgumentType"
        )
        raise ValueError(msg)
    line: int | None = None
    if sep:
        try:
            line = int(line_part)
        except ValueError:
            msg = f"expect-type-error line must be an integer, got {line_part!r}"
            raise ValueError(msg) from None
        if line < 1:
            msg = f"expect-type-error line must be >= 1, got {line}"
            raise ValueError(msg)
    return ExpectedDiagnostic(rule=rule, line=line)


def _parse_directives(
    pending: list[str],
) -> tuple[frozenset[str], tuple[ExpectedDiagnostic, ...]]:
    directives: set[str] = set()
    expected: list[ExpectedDiagnostic] = []
    for body in pending:
        for raw in body.split(","):
            name = raw.strip()
            if not name:
                continue
            base, sep, arg = name.partition("=")
            if base == "expect-type-error" and sep:
                expected.append(_parse_expected_diagnostic(arg))
                continue
            if name not in VALID_DIRECTIVES:
                msg = (
                    f"Unknown snektest-doc directive {name!r}; "
                    f"valid directives are: {', '.join(sorted(VALID_DIRECTIVES))}"
                )
                raise ValueError(msg)
            directives.add(name)
    if "skip-typecheck" in directives and (
        expected or "expect-type-error" in directives
    ):
        msg = (
            "skip-typecheck cannot be combined with an expect-type-error "
            "expectation: the block is never type-checked, so the expectation "
            "would silently never be asserted"
        )
        raise ValueError(msg)
    return frozenset(directives), tuple(expected)


def extract_blocks(text: str, source: str) -> list[CodeBlock]:
    """Extract every fenced code block from ``text`` in document order."""
    blocks: list[CodeBlock] = []
    lines = text.splitlines()
    pending_directives: list[str] = []
    i = 0
    index = 0
    while i < len(lines):
        line = lines[i]
        directive_match = DIRECTIVE_RE.search(line)
        if directive_match is not None:
            pending_directives.append(directive_match.group("body"))
            i += 1
            continue

        fence_match = FENCE_RE.match(line)
        if fence_match is None:
            if line.strip():
                # A non-blank, non-directive line breaks the directive run.
                pending_directives.clear()
            i += 1
            continue

        indent = fence_match.group("indent")
        lang = fence_match.group("lang")
        fence_line = i + 1  # 1-based
        body_lines: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() != "```":
            body_lines.append(lines[i].removeprefix(indent))
            i += 1
        i += 1  # consume closing fence

        directives, expected = _parse_directives(pending_directives)
        blocks.append(
            CodeBlock(
                source=source,
                lang=lang,
                code="\n".join(body_lines) + ("\n" if body_lines else ""),
                line=fence_line,
                index=index,
                directives=directives,
                expected_diagnostics=expected,
            )
        )
        index += 1
        pending_directives.clear()

    return _attach_following_text(blocks)


def _attach_following_text(blocks: list[CodeBlock]) -> list[CodeBlock]:
    resolved: list[CodeBlock] = []
    for position, block in enumerate(blocks):
        following: str | None = None
        if block.lang == "python":
            nxt = blocks[position + 1] if position + 1 < len(blocks) else None
            if nxt is not None and nxt.lang == "text":
                following = nxt.code
        resolved.append(
            CodeBlock(
                source=block.source,
                lang=block.lang,
                code=block.code,
                line=block.line,
                index=block.index,
                directives=block.directives,
                expected_diagnostics=block.expected_diagnostics,
                following_text=following,
            )
        )
    return resolved


def python_blocks(text: str, source: str) -> list[CodeBlock]:
    """Return only the ```python blocks from ``text``."""
    return [block for block in extract_blocks(text, source) if block.lang == "python"]


def doc_python_blocks() -> list[CodeBlock]:
    """Collect ```python blocks from every documentation surface in scope."""
    repo_root = Path(__file__).resolve().parent.parent
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    return [
        *python_blocks(readme, "README.md"),
        *python_blocks(AGENT_DOCS, "agent_docs.py"),
    ]
