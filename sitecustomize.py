from __future__ import annotations

"""Auto-start coverage in subprocesses.

When `COVERAGE_PROCESS_START` is set (e.g. to `pyproject.toml`), Coverage.py can
auto-enable measurement in child Python processes. This is critical for tests
that validate snektest via subprocesses.

See: https://coverage.readthedocs.io/en/latest/subprocess.html
"""

try:
    import coverage
except ModuleNotFoundError:
    coverage = None


def _maybe_start_coverage() -> None:
    if coverage is None:
        return

    _ = coverage.process_startup()


_maybe_start_coverage()
