"""Typing contracts across the real optional-extra installation boundary."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, assert_true, load_fixture, test
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
    _check_selected_environment_isolation(python, directory)
    if os.name != "nt":
        _check_native_startup_failure(python, directory)


def _site_packages(python: Path, directory: Path) -> Path:
    located = _run(
        [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        cwd=directory,
    )
    assert_eq(located.returncode, 0, msg=located.stderr)
    return Path(located.stdout.strip())


def _check_selected_environment_isolation(python: Path, directory: Path) -> None:
    target = directory / "isolated-typing-python"
    created = _run(["uv", "venv", "--python", str(python), str(target)], cwd=directory)
    assert_eq(created.returncode, 0, msg=created.stderr)
    target_python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    host_site = _site_packages(python, directory)
    target_site = _site_packages(target_python, directory)
    (host_site / "snektest_host_only_probe.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    config = directory / "isolated-environment.toml"
    config.write_text(
        f"[environment]\npython = {json.dumps(str(target))}\n", encoding="utf-8"
    )
    consumer = directory / "test_environment_isolation.py"
    source = (
        "from typing import assert_type\nfrom snektest import test_typing\n"
        "@test_typing\ndef consumer() -> None:\n"
        "    from snektest_host_only_probe import value\n"
        "    assert_type(value(), EXPECTED)\n"
    )
    consumer.write_text(source.replace("EXPECTED", "int"), encoding="utf-8")
    command = [
        str(python),
        "-I",
        "-m",
        "snektest",
        "--json-output",
        "--typing-config",
        str(config),
        str(consumer),
    ]
    missing = _run(command, cwd=directory)
    assert_eq(missing.returncode, 1, msg=missing.stdout + missing.stderr)
    result = json.loads(missing.stdout)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 0))
    assert_in("snektest_host_only_probe", result["tests"][0]["exception"]["message"])
    (target_site / "snektest_host_only_probe.pyi").write_text(
        "def value() -> str: ...\n", encoding="utf-8"
    )
    consumer.write_text(source.replace("EXPECTED", "str"), encoding="utf-8")
    selected = _run(command, cwd=directory)
    assert_eq(selected.returncode, 0, msg=selected.stdout + selected.stderr)
    assert_eq(json.loads(selected.stdout)["passed"], 1)


def _check_native_startup_failure(python: Path, directory: Path) -> None:
    located = _run(
        [str(python), "-I", "-c", "import ty; print(ty.find_ty_bin())"], cwd=directory
    )
    assert_eq(located.returncode, 0, msg=located.stderr)
    binary = Path(located.stdout.strip())
    assert_true(binary.is_relative_to(directory))
    # Break any uv-cache hardlink before changing permissions. Only this
    # disposable environment's directory entry is replaced, never a shared inode.
    disabled = binary.with_name(f"{binary.name}.disabled")
    shutil.copyfile(binary, disabled)
    disabled.chmod(0o600)
    disabled.replace(binary)
    failed = _run(
        [str(python), "-I", "-m", "snektest", "--json-output", "test_consumer.py"],
        cwd=directory,
    )
    assert_eq(failed.returncode, 1, msg=failed.stdout + failed.stderr)
    result = json.loads(failed.stdout)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_eq(result["tests"][0]["exception"]["type"], "PermissionError")


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
