"""Helper functions for testing snektest itself."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def create_test_file(
    tmp_dir: Path, content: str, *, name: str = "test_generated"
) -> Path:
    """Create a temporary Python test file.

    Args:
        tmp_dir: Directory to create file in
        content: Python code as string (use textwrap.dedent!)
        name: Filename without .py extension

    Returns:
        Path to created file
    """
    filepath = tmp_dir / f"{name}.py"
    _ = filepath.write_text(content)
    return filepath


def run_test_subprocess(test_file: Path) -> dict[str, Any]:
    """Run snektest subprocess on test file and return structured results.

    Args:
        test_file: Path to test file to run

    Returns:
        Dict with keys: passed, failed, fixture_teardown_failed,
                       session_teardown_failed, returncode
    """
    cmd = [sys.executable, "-m", "snektest.cli", "--json-output", str(test_file)]

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=0.5,
    )

    lines = result.stdout.strip().split("\n")
    json_line = lines[-1]
    results = json.loads(json_line)
    results["returncode"] = result.returncode
    return results
