"""Interpreter policy for standalone entry points, never library imports."""

lazy import os
lazy import subprocess
lazy import sys

lazy from snektest.cli import main as cli_main


def main() -> None:
    """Enter the CLI with context-aware warnings enabled unless opted out."""
    if (
        not sys.flags.context_aware_warnings
        and "context_aware_warnings" not in sys._xoptions  # noqa: SLF001 -- documented CPython startup options
        and (
            sys.flags.ignore_environment
            or not os.environ.get("PYTHON_CONTEXT_AWARE_WARNINGS")
        )
    ):
        command = [
            sys.executable,
            "-X",
            "context_aware_warnings=1",
            *sys.orig_argv[1:],
        ]
        sys.stdout.flush()
        sys.stderr.flush()
        if os.name != "nt":
            os.execv(sys.executable, command)  # noqa: S606 -- same interpreter, original argument boundaries
        # Windows has no POSIX process replacement. Keep the original console,
        # streams and exit status; let console interrupts reach the child too.
        with subprocess.Popen(command) as child:  # noqa: S603 -- same interpreter, no shell
            while True:
                try:
                    status = child.wait()
                    break
                except KeyboardInterrupt:
                    continue
        raise SystemExit(status)

    cli_main()
