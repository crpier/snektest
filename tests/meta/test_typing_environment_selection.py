"""Native config selects an explicit checker environment and import roots."""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from snektest import Param, assert_eq, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_native_config_can_select_python_environment_and_extra_import_roots(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture()) / f"selection-{workers}"
    directory.mkdir()
    environment = directory / "target-python"
    created = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(environment)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(created.returncode, 0, msg=created.stderr)
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    paths = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(paths.returncode, 0, msg=paths.stderr)
    site_packages = Path(paths.stdout.strip())
    (site_packages / "target_only.pyi").write_text(
        "def convert(value: int) -> int: ...\n", encoding="utf-8"
    )
    roots = directory / "extra-stubs"
    roots.mkdir()
    (roots / "roots_only.pyi").write_text(
        "def render(value: int) -> str: ...\n", encoding="utf-8"
    )
    project = directory / "consumer-project"
    project.mkdir()
    path = create_test_file(
        project,
        dedent("""
        from typing import assert_type
        from snektest import test_typing

        @test_typing
        def consumer(symbolic: int) -> None:
            from target_only import convert
            from roots_only import render
            assert_type(convert(symbolic), int)
            assert_type(render(symbolic), str)
        """),
        name="test_selected_environment",
    )
    config = directory / "environment.toml"
    config.write_text(
        f"[environment]\npython = {json.dumps(str(environment))}\nextra-paths = [{json.dumps(str(roots))}]\n",
        encoding="utf-8",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(
        path, "--typing-config", str(config), *arguments, timeout=30
    )
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    unconfigured = run_test_subprocess(path, timeout=30)
    assert_eq((unconfigured["errors"], unconfigured["passed"]), (1, 0))
