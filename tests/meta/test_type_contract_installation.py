"""Typing contracts across the real optional-extra installation boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@test([Param("wheel", name="wheel"), Param("sdist", name="sdist")], mark="slow")
def test_base_install_collects_without_ty_and_the_typing_extra_enables_execution(
    archive_kind: str,
) -> None:
    directory = Path(mkdtemp(dir=load_fixture(tmp_dir_fixture())))
    distribution = directory / "dist"
    built = _run(
        ["uv", "build", f"--{archive_kind}", "--out-dir", str(distribution)],
        cwd=Path.cwd(),
    )
    assert_eq(built.returncode, 0, msg=built.stdout + built.stderr)
    archives = list(
        distribution.glob("*.whl" if archive_kind == "wheel" else "*.tar.gz")
    )
    assert_eq(len(archives), 1)
    environment = directory / "venv"
    created = _run(
        ["uv", "venv", "--python", sys.executable, str(environment)], cwd=directory
    )
    assert_eq(created.returncode, 0, msg=created.stderr)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = _run(
        ["uv", "pip", "install", "--python", str(python), str(archives[0])],
        cwd=directory,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)
    absent = _run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from importlib.util import find_spec; "
                "from snektest import assert_is_none; assert_is_none(find_spec('ty'))"
            ),
        ],
        cwd=directory,
    )
    assert_eq(absent.returncode, 0, msg=absent.stdout + absent.stderr)
    _check_base_commands(python, directory)
    enabled = _run(
        ["uv", "pip", "install", "--python", str(python), f"{archives[0]}[typing]"],
        cwd=directory,
    )
    assert_eq(enabled.returncode, 0, msg=enabled.stdout + enabled.stderr)
    executed = _run(
        [str(python), "-I", "-m", "snektest", "--json-output", "test_consumer.py"],
        cwd=directory,
    )
    assert_eq(executed.returncode, 0, msg=executed.stdout + executed.stderr)
    assert_eq(json.loads(executed.stdout)["passed"], 2)


def _check_base_commands(python: Path, directory: Path) -> None:
    (directory / "test_consumer.py").write_text(
        dedent("""
        from typing import assert_type
        from snektest import assert_eq, assert_type_error, test, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def contract(symbolic: int) -> None:
            assert_type(takes_int(symbolic), int)
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")

        @test(mark="fast")
        def normal_case() -> None:
            assert_eq(takes_int(1), 1)
        """),
        encoding="utf-8",
    )
    command = [str(python), "-I", "-m", "snektest", "--json-output", "test_consumer.py"]
    collected = _run([*command, "--collect-only"], cwd=directory)
    assert_eq(collected.returncode, 0, msg=collected.stdout + collected.stderr)
    assert_eq(json.loads(collected.stdout)["kind"], "collection")
    assert_eq(json.loads(collected.stdout)["total_tests"], 2)
    normal = _run([*command, "-k", "normal_case"], cwd=directory)
    assert_eq(normal.returncode, 0, msg=normal.stdout + normal.stderr)
    assert_eq(json.loads(normal.stdout)["passed"], 1)
    (directory / "invalid-ty.toml").write_text("[rules", encoding="utf-8")
    for selection in (["--collect-only"], ["-k", "normal_case"]):
        unused_config = _run(
            [*command, "--typing-config", "invalid-ty.toml", *selection], cwd=directory
        )
        assert_eq(
            unused_config.returncode, 0, msg=unused_config.stdout + unused_config.stderr
        )
    missing = _run(command, cwd=directory)
    assert_eq(missing.returncode, 1, msg=missing.stdout + missing.stderr)
    result = json.loads(missing.stdout)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_in("snektest[typing]", result["tests"][0]["exception"]["message"])
