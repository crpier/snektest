"""Type-check and run the code blocks extracted from documentation surfaces.

This spawns ``pyright`` and ``snektest`` subprocesses, so it is kept separate
from the pure extraction logic in :mod:`testutils.docblocks`.

Type-checking happens in a single batched ``pyright`` invocation: every block
is written to its own file inside a temporary directory under the repo root
(so ``pyright`` discovers this repo's strict config and resolves ``snektest``),
then diagnostics are mapped back to each block by file name.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from testutils.docblocks import CodeBlock

REPO_ROOT = Path(__file__).resolve().parent.parent

# Volatile output that must be normalized away before diffing documented
# ```text blocks against captured output.
_DURATION_RE = re.compile(r"\d+\.\d+s")
_TMP_PATH_RE = re.compile(r"/[^\s:]*?/(test_[\w.]+\.py)")


def _block_filename(block: CodeBlock) -> str:
    """Filesystem-safe, ``test_``-prefixed name unique per block."""
    stem = block.source.replace(".", "_")
    return f"test_{stem}_{block.index:02d}.py"


@dataclass(frozen=True)
class Diagnostic:
    """A single pyright error reported against a block."""

    rule: str
    """Pyright rule name, e.g. ``reportArgumentType``; ``""`` for syntax errors."""
    line: int
    """1-based line within the block (pyright's 0-based range start + 1)."""
    message: str
    """First line of the pyright message."""


@dataclass(frozen=True)
class TypecheckResult:
    """Pyright diagnostics for a single block."""

    diagnostics: list[Diagnostic]

    @property
    def error_count(self) -> int:
        return len(self.diagnostics)

    @property
    def messages(self) -> list[str]:
        return [
            f"{d.rule}: {d.message}" if d.rule else d.message for d in self.diagnostics
        ]


def check_block_diagnostics(block: CodeBlock, result: TypecheckResult) -> list[str]:
    """Compare a block's pyright result against its expectations.

    Returns a list of human-readable problems (empty when the block matches):

    - pinned ``expected_diagnostics``: every one must match a reported
      diagnostic (rule equal; line equal too when given). Extra unexpected
      errors are tolerated (cascading diagnostics are common).
    - a bare ``expect-type-error`` flag: at least one error must be reported.
    - neither: pyright must report no errors.
    """
    where = f"{block.slug} ({block.source}:{block.line})"
    bare = "expect-type-error" in block.directives
    problems: list[str] = []

    if block.expected_diagnostics:
        found = ", ".join(f"{d.rule}@{d.line}" for d in result.diagnostics) or "none"
        for exp in block.expected_diagnostics:
            matched = any(
                d.rule == exp.rule and (exp.line is None or d.line == exp.line)
                for d in result.diagnostics
            )
            if not matched:
                problems.append(
                    f"{where} expected diagnostic {exp} but pyright found: {found}"
                )
    elif bare:
        if result.error_count == 0:
            problems.append(
                f"{where} is marked expect-type-error but pyright found none."
            )
    elif result.error_count:
        joined = "\n  ".join(result.messages)
        problems.append(f"{where} failed pyright:\n  {joined}")

    return problems


def typecheck_blocks(blocks: Sequence[CodeBlock]) -> dict[str, TypecheckResult]:
    """Run pyright once over every block; return results keyed by slug."""
    diagnostics: dict[str, list[Diagnostic]] = {block.slug: [] for block in blocks}
    if not blocks:
        return {}

    with tempfile.TemporaryDirectory(dir=REPO_ROOT, prefix="doccheck_") as tmp:
        tmp_path = Path(tmp)
        file_to_slug: dict[str, str] = {}
        paths: list[str] = []
        for block in blocks:
            block_path = tmp_path / _block_filename(block)
            _ = block_path.write_text(block.code, encoding="utf-8")
            file_to_slug[str(block_path.resolve())] = block.slug
            paths.append(str(block_path))

        proc = subprocess.run(
            [sys.executable, "-m", "pyright", "--outputjson", *paths],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        data = json.loads(proc.stdout)

    for diag in data["generalDiagnostics"]:
        if diag.get("severity") != "error":
            continue
        slug = file_to_slug.get(str(Path(diag["file"]).resolve()))
        if slug is None:
            continue
        diagnostics[slug].append(
            Diagnostic(
                rule=diag.get("rule", ""),
                line=diag["range"]["start"]["line"] + 1,
                message=diag["message"].splitlines()[0],
            )
        )

    return {block.slug: TypecheckResult(diagnostics[block.slug]) for block in blocks}


def run_block(
    block: CodeBlock, *, timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    """Execute a single doc block with snektest and return the process."""
    with tempfile.TemporaryDirectory() as tmp:
        block_path = Path(tmp) / _block_filename(block)
        _ = block_path.write_text(block.code, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "snektest.cli", str(block_path)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def normalize_output(text: str) -> str:
    """Strip volatile output (durations, temp paths, trailing whitespace)."""
    text = _DURATION_RE.sub("Xs", text)
    text = _TMP_PATH_RE.sub(r"\1", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines)
