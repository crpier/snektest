"""Run the repository's complete release-health gate."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

_RELEASE_STAGES = (
    "lock",
    "format",
    "lint",
    "types",
    "dependency-audit",
    "tests-with-coverage",
    "coverage-threshold",
)
_ROOT = Path(__file__).resolve().parent.parent


async def _run_command(
    stage: str,
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run one gate command without a shell so every platform sees the same argv."""
    print(f"==> {stage}", flush=True)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=_ROOT,
        env=environment,
    )
    return await process.wait()


async def _run_release_gate() -> int:
    for stage, command in (
        ("lock", ("uv", "lock", "--check")),
        ("format", ("uv", "run", "--locked", "ruff", "format", "--check", ".")),
        ("lint", ("uv", "run", "--locked", "ruff", "check", ".")),
        ("types", ("uv", "run", "--locked", "ty", "check")),
        ("coverage reset", ("uv", "run", "--locked", "coverage", "erase")),
    ):
        if exit_code := await _run_command(stage, command):
            return exit_code

    with tempfile.TemporaryDirectory(prefix="snektest-audit-") as audit_directory:
        requirements = Path(audit_directory) / "requirements.txt"
        for stage, command in (
            (
                "dependency-audit export",
                (
                    "uv",
                    "export",
                    "--quiet",
                    "--locked",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                    "--output-file",
                    str(requirements),
                ),
            ),
            (
                "dependency-audit",
                (
                    "uv",
                    "run",
                    "--locked",
                    "pip-audit",
                    "--requirement",
                    str(requirements),
                    "--progress-spinner",
                    "off",
                ),
            ),
        ):
            if exit_code := await _run_command(stage, command):
                return exit_code

    coverage_environment = dict(os.environ)
    coverage_environment.update(
        {
            "COVERAGE_FILE": str(_ROOT / ".coverage"),
            "COVERAGE_PROCESS_START": str(_ROOT / "pyproject.toml"),
        }
    )
    for stage, command in (
        (
            "tests-with-coverage",
            (
                "uv",
                "run",
                "--locked",
                "coverage",
                "run",
                "-m",
                "snektest",
                "tests",
            ),
        ),
        (
            "coverage combine",
            ("uv", "run", "--locked", "coverage", "combine", "--quiet"),
        ),
        (
            "coverage-threshold",
            ("uv", "run", "--locked", "coverage", "report"),
        ),
    ):
        if exit_code := await _run_command(
            stage,
            command,
            environment=coverage_environment,
        ):
            return exit_code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate, or list its stable user-visible stages with `--list`."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("--list",):
        print("\n".join(_RELEASE_STAGES))
        return 0
    if arguments:
        print("Usage: release_check.py [--list]", file=sys.stderr)
        return 2
    return asyncio.run(_run_release_gate())


if __name__ == "__main__":
    raise SystemExit(main())
