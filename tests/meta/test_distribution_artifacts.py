"""Release-artifact manifest, metadata, installation, and version tests."""

from __future__ import annotations

import email.parser
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from snektest import assert_eq, assert_in, assert_true, load_fixture, test
from testutils.fixtures import tmp_dir_fixture

_VERSION = "0.17.0.dev0"
_TIMEOUT_SECONDS = 60
_REQUIRED_PROJECT_FILES = {
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
}
_REQUIRED_EXAMPLES = {
    "snektest/examples/__init__.py",
    "snektest/examples/async_tests.py",
    "snektest/examples/basic_test.py",
    "snektest/examples/benchmark.py",
    "snektest/examples/fixtures.py",
    "snektest/examples/memory.py",
    "snektest/examples/parametrize.py",
    "snektest/examples/schema.py",
}


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


def _strip_sdist_root(members: list[str]) -> set[str]:
    roots = {member.split("/", maxsplit=1)[0] for member in members}
    assert_eq(len(roots), 1)
    root = next(iter(roots))
    return {
        member.removeprefix(f"{root}/")
        for member in members
        if member != root and not member.endswith("/")
    }


def _package_files(source: Path) -> set[str]:
    package = source / "snektest"
    return {
        path.relative_to(source).as_posix()
        for path in package.rglob("*")
        if path.is_file()
        and (path.suffix in {".py", ".pyi"} or path.name == "py.typed")
    }


def _assert_install_smoke(artifact: Path, *, target: Path, cwd: Path) -> None:
    installed = _run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(target),
            "--no-deps",
            str(artifact),
        ],
        cwd=cwd,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)

    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "import snektest; "
        "print(json.dumps({'version': snektest.__version__}))"
    )
    imported = _run([sys.executable, "-I", "-c", script], cwd=cwd)
    assert_eq(imported.returncode, 0, msg=imported.stderr)
    assert_eq(json.loads(imported.stdout), {"version": _VERSION})

    cli_script = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "sys.argv = ['snektest', '--version']; "
        "runpy.run_module('snektest', run_name='__main__')"
    )
    invoked = _run([sys.executable, "-I", "-c", cli_script], cwd=cwd)
    assert_eq(invoked.returncode, 0, msg=invoked.stderr)
    assert_eq(invoked.stdout, f"snektest {_VERSION}\n")


def _assert_wheel_manifest_and_metadata(
    wheel: Path, expected_package_files: set[str]
) -> None:
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = set(archive.namelist())
        metadata_path = next(
            member for member in wheel_members if member.endswith(".dist-info/METADATA")
        )
        metadata_text = archive.read(metadata_path).decode()
    assert_true(wheel_members >= _REQUIRED_EXAMPLES)
    assert_true("snektest/py.typed" in wheel_members)
    assert_eq(
        {member for member in wheel_members if member.startswith("snektest/")},
        expected_package_files,
    )
    assert_true(
        any(member.endswith(".dist-info/licenses/LICENSE") for member in wheel_members)
    )
    assert_true(
        all(
            member.startswith("snektest/") or ".dist-info/" in member
            for member in wheel_members
        )
    )

    metadata = email.parser.Parser().parsestr(metadata_text)
    assert_eq(metadata["Name"], "snektest")
    assert_eq(metadata["Version"], _VERSION)
    assert_eq(
        metadata["Summary"],
        "An async-native, type-safe Python testing framework",
    )
    assert_eq(metadata["Author"], None)
    assert_eq(metadata["Author-email"], "crpier42@gmail.com")
    assert_eq(metadata["License-Expression"], "MIT")
    assert_eq(metadata["Requires-Python"], ">=3.14")
    project_urls = metadata.get_all("Project-URL")
    assert_in("Repository, https://github.com/crpier/snektest", project_urls)
    assert_in("Issues, https://github.com/crpier/snektest/issues", project_urls)
    classifiers = metadata.get_all("Classifier")
    for classifier in (
        "Operating System :: MacOS",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.14",
        "Programming Language :: Python :: Implementation :: CPython",
    ):
        assert_in(classifier, classifiers)
    assert_in("# snektest", metadata.get_payload())


def _assert_sdist_manifest(sdist: Path, expected_package_files: set[str]) -> None:
    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_members = _strip_sdist_root(archive.getnames())
    assert_true(sdist_members >= _REQUIRED_PROJECT_FILES)
    assert_true(sdist_members >= _REQUIRED_EXAMPLES)
    assert_true("snektest/py.typed" in sdist_members)
    assert_eq(
        {member for member in sdist_members if member.startswith("snektest/")},
        expected_package_files,
    )
    assert_true(
        all(
            member in _REQUIRED_PROJECT_FILES
            or member == "PKG-INFO"
            or member.startswith("snektest/")
            for member in sdist_members
        )
    )
    assert_true(all(".claude" not in member for member in sdist_members))
    assert_true(all("test_test.py" not in member for member in sdist_members))
    assert_true(all("reports/" not in member for member in sdist_members))


@test(mark="slow")
def test_wheel_and_sdist_are_intentional_and_independently_installable() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    source = Path.cwd()
    project = tmp_dir / "project"
    project.mkdir()
    for project_file in _REQUIRED_PROJECT_FILES:
        _ = shutil.copy2(source / project_file, project / project_file)
    _ = shutil.copytree(source / "snektest", project / "snektest")
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text("{}")
    (project / "test_scratch.py").write_text("SECRET = True\n")
    (project / "snektest" / "workstation-secret.txt").write_text("secret\n")
    expected_package_files = _package_files(source)

    distribution_dir = tmp_dir / "dist"
    built = _run(
        ["uv", "build", "--out-dir", str(distribution_dir)],
        cwd=project,
    )
    assert_eq(built.returncode, 0, msg=built.stdout + built.stderr)

    wheels = list(distribution_dir.glob("*.whl"))
    sdists = list(distribution_dir.glob("*.tar.gz"))
    assert_eq(len(wheels), 1)
    assert_eq(len(sdists), 1)
    wheel = wheels[0]
    sdist = sdists[0]
    assert_in(_VERSION, wheel.name)
    assert_in(_VERSION, sdist.name)

    _assert_wheel_manifest_and_metadata(wheel, expected_package_files)
    _assert_sdist_manifest(sdist, expected_package_files)
    _assert_install_smoke(wheel, target=tmp_dir / "wheel-install", cwd=tmp_dir)
    _assert_install_smoke(sdist, target=tmp_dir / "sdist-install", cwd=tmp_dir)
