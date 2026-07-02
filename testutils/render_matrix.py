"""Render snektest console output across a matrix of terminal shapes.

Rich derives its render width from ``COLUMNS``/``LINES`` in the environment even
when stdout is a pipe, so this spawns the CLI once per shape with those set and
captures the result. Use it to eyeball how the presentation layer wraps,
truncates, and draws rules at different window sizes, or to hand the collected
``MANIFEST.md`` to an agent for review.

Examples::

    uv run python -m testutils.render_matrix tests/isolated/test_basic.py
    uv run python -m testutils.render_matrix --color <filters>
    uv run python -m testutils.render_matrix --sizes 20x10,80x24,200x50 <filters>

Outputs land in ``./render_out/`` (override with ``--out``):
    ``<cols>x<lines>.txt``   raw capture per shape
    ``MANIFEST.md``          every shape concatenated and labeled, ANSI stripped
                             unless ``--color`` is set
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SIZES: list[tuple[int, int]] = [
    (20, 10),
    (40, 20),
    (80, 24),
    (120, 40),
    (200, 60),
]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_sizes(raw: str) -> list[tuple[int, int]]:
    """Parse ``"20x10,80x24"`` into ``[(20, 10), (80, 24)]``; height defaults to 24."""
    sizes: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        cols, _, lines = chunk.strip().partition("x")
        sizes.append((int(cols), int(lines or "24")))
    return sizes


def render(cols: int, lines: int, args: list[str], *, color: bool) -> str:
    """Run the snektest CLI in a subprocess pinned to a terminal shape."""
    env = os.environ.copy()
    env["COLUMNS"] = str(cols)
    env["LINES"] = str(lines)
    if color:
        env["FORCE_COLOR"] = "1"
        _ = env.pop("NO_COLOR", None)
    else:
        _ = env.pop("FORCE_COLOR", None)
        env["NO_COLOR"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "snektest", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_matrix",
        description="Render snektest output across terminal shapes.",
    )
    _ = parser.add_argument(
        "--sizes",
        default=None,
        help="Comma-separated COLSxLINES list, e.g. 20x10,80x24,200x50.",
    )
    _ = parser.add_argument(
        "--color",
        action="store_true",
        help="Keep ANSI color codes instead of stripping them.",
    )
    _ = parser.add_argument(
        "--out",
        default="render_out",
        help="Output directory (default: render_out).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    known, passthrough = parser.parse_known_args(argv)

    sizes = parse_sizes(known.sizes) if known.sizes else DEFAULT_SIZES
    out_dir = Path(known.out)
    out_dir.mkdir(exist_ok=True)

    manifest: list[str] = []
    for cols, lines in sizes:
        raw = render(cols, lines, passthrough, color=known.color)
        _ = (out_dir / f"{cols}x{lines}.txt").write_text(raw)
        body = raw if known.color else _ANSI.sub("", raw)
        manifest.append(f"## shape {cols}x{lines} (cols x lines)\n\n```\n{body}\n```\n")
        print(f"rendered {cols}x{lines} -> {out_dir / f'{cols}x{lines}.txt'}")

    _ = (out_dir / "MANIFEST.md").write_text("\n".join(manifest))
    print(f"\nmanifest: {out_dir / 'MANIFEST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
