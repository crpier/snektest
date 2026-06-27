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
class TypecheckResult:
    """Pyright diagnostics for a single block."""

    error_count: int
    messages: list[str]


def typecheck_blocks(blocks: Sequence[CodeBlock]) -> dict[str, TypecheckResult]:
    """Run pyright once over every block; return results keyed by slug."""
    counts: dict[str, int] = {block.slug: 0 for block in blocks}
    messages: dict[str, list[str]] = {block.slug: [] for block in blocks}
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
        counts[slug] += 1
        rule = diag.get("rule", "")
        first_line = diag["message"].splitlines()[0]
        messages[slug].append(f"{rule}: {first_line}" if rule else first_line)

    return {
        block.slug: TypecheckResult(counts[block.slug], messages[block.slug])
        for block in blocks
    }


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
