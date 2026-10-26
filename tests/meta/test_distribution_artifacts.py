"""Release-artifact manifest, metadata, installation, and version tests."""

from __future__ import annotations

import email.parser
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from snektest import assert_eq, assert_in, assert_ne, assert_true, load_fixture, test
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
    "snektest/examples/anyio_tests.py",
    "snektest/examples/basic_test.py",
    "snektest/examples/benchmark.py",
    "snektest/examples/fixtures.py",
    "snektest/examples/temporary.py",
    "snektest/temporary.py",
    "snektest/examples/graphql.py",
    "snektest/examples/log_capture.py",
    "snektest/examples/memory.py",
    "snektest/examples/outcomes.py",
    "snektest/examples/parametrize.py",
    "snektest/examples/schema.py",
}


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        env=environment,
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
            "--python",
            sys.executable,
            "--target",
            str(target),
            "--no-deps",
            str(artifact),
        ],
        cwd=cwd,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)

    rejected = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--python-version",
            "3.14",
            "--target",
            str(target.with_name(f"{target.name}-python314")),
            "--no-deps",
            "--dry-run",
            str(artifact),
        ],
        cwd=cwd,
    )
    assert_ne(rejected.returncode, 0, msg=rejected.stdout + rejected.stderr)
    assert_in("does not satisfy Python>=3.15", rejected.stderr)

    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "import snektest; "
        "print(json.dumps({'version': snektest.__version__}))"
    )
    cli_script = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "sys.argv = ['snektest', '--version']; "
        "runpy.run_module('snektest', run_name='__main__')"
    )
    test_path = cwd / f"test_{target.name.replace('-', '_')}.py"
    test_path.write_text(
        "import logging\n"
        "from collections.abc import Generator\n"
        "from hypothesis import settings, strategies as st\n"
        "from snektest import assert_eq, capture_logs, fixture, load_fixture, test_hypothesis\n"
        "@fixture(scope='run')\n"
        "def descriptor() -> Generator[int]:\n"
        "    yield 7\n"
        "@settings(max_examples=1, database=None, deadline=None)\n"
        "@test_hypothesis(st.just(7), mark='fast')\n"
        "async def property_case(expected: int) -> None:\n"
        "    assert_eq(load_fixture(descriptor()), expected)\n"
        "    with capture_logs('installed.artifact') as logs:\n"
        "        logging.getLogger('installed.artifact').info('artifact capture')\n"
        "    assert_eq(logs.messages, ('artifact capture',))\n"
    )
    property_script = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        f"sys.argv = ['snektest', '--json-output', '--workers', '1', {str(test_path)!r}]; "
        "runpy.run_module('snektest', run_name='__main__')"
    )
    for mode in ("normal", "all"):
        arguments = [sys.executable, "-I", "-X", f"lazy_imports={mode}", "-c"]
        imported = _run([*arguments, script], cwd=cwd)
        assert_eq(imported.returncode, 0, msg=imported.stderr)
        assert_eq(json.loads(imported.stdout), {"version": _VERSION})
        invoked = _run([*arguments, cli_script], cwd=cwd)
        assert_eq(invoked.returncode, 0, msg=invoked.stderr)
        assert_eq(invoked.stdout, f"snektest {_VERSION}\n")
        executed = _run([*arguments, property_script], cwd=cwd)
        assert_eq(executed.returncode, 0, msg=executed.stdout + executed.stderr)
        assert_eq(json.loads(executed.stdout)["passed"], 1)


def _assert_standalone_cli_startup(artifact: Path, *, target: Path, cwd: Path) -> None:
    environment_root = target.with_name(f"{target.name}-venv")
    created = _run(
        ["uv", "venv", "--python", sys.executable, str(environment_root)], cwd=cwd
    )
    assert_eq(created.returncode, 0, msg=created.stdout + created.stderr)
    binary = environment_root / ("Scripts" if os.name == "nt" else "bin")
    python = binary / ("python.exe" if os.name == "nt" else "python")
    executable = binary / ("snektest.exe" if os.name == "nt" else "snektest")
    constraints = cwd / f"{target.name}-constraints.txt"
    exported = _run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--extra",
            "anyio",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            str(constraints),
        ],
        cwd=Path.cwd(),
    )
    assert_eq(exported.returncode, 0, msg=exported.stderr)
    installed = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraints",
            str(constraints),
            str(artifact),
        ],
        cwd=cwd,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)
    consumer = cwd / f"test_{target.name.replace('-', '_')}_startup.py"
    consumer.write_text(
        "import sys\n"
        "from snektest import assert_eq, test\n"
        "assert_eq(sys.flags.context_aware_warnings, 1)\n"
        "@test(mark='fast')\n"
        "def startup_policy() -> None:\n"
        "    assert_eq(sys.flags.context_aware_warnings, 1)\n"
    )
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    for entry in ([str(executable)], [str(python), "-I", "-m", "snektest"]):
        executed = subprocess.run(
            [*entry, "--json-output", str(consumer)],
            cwd=cwd,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert_eq(executed.returncode, 0, msg=executed.stdout + executed.stderr)
        assert_eq(json.loads(executed.stdout)["passed"], 1)

    _assert_optional_anyio(artifact, python=python, constraints=constraints, cwd=cwd)


def _assert_optional_anyio(
    artifact: Path, *, python: Path, constraints: Path, cwd: Path
) -> None:
    """Exercise the extra independently, after the core-only installation checks."""
    absent = _run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from importlib.util import find_spec; "
                "from snektest import assert_is_none; assert_is_none(find_spec('anyio'))"
            ),
        ],
        cwd=cwd,
    )
    assert_eq(absent.returncode, 0, msg=absent.stdout + absent.stderr)
    installed = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraints",
            str(constraints),
            f"{artifact}[anyio]",
        ],
        cwd=cwd,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)
    consumer = cwd / f"test_{python.parent.parent.name.replace('-', '_')}_anyio.py"
    consumer.write_text(
        "import asyncio\n"
        "import anyio\n"
        "import snektest\n"
        "from collections.abc import AsyncGenerator\n"
        "from anyio.to_thread import run_sync\n"
        "from snektest import assert_eq, assert_in, assert_is, fixture, load_fixture, test\n"
        "assert_in('site-packages', snektest.__file__ or '')\n"
        "@fixture\n"
        "async def resource() -> AsyncGenerator[int]:\n"
        "    owner = asyncio.current_task()\n"
        "    async with anyio.create_task_group() as group:\n"
        "        group.start_soon(anyio.sleep_forever)\n"
        "        try:\n"
        "            yield await run_sync(lambda: 42)\n"
        "        finally:\n"
        "            assert_is(asyncio.current_task(), owner)\n"
        "            group.cancel_scope.cancel()\n"
        "@test(mark='medium')\n"
        "async def offload() -> None:\n"
        "    assert_eq(await load_fixture(resource()), 42)\n"
    )
    for mode in ("normal", "all"):
        for arguments in ([], ["--workers", "1"]):
            executed = _run(
                [
                    str(python),
                    "-I",
                    "-X",
                    f"lazy_imports={mode}",
                    "-m",
                    "snektest",
                    "--json-output",
                    *arguments,
                    str(consumer),
                ],
                cwd=cwd,
            )
            assert_eq(executed.returncode, 0, msg=executed.stdout + executed.stderr)
            assert_eq(json.loads(executed.stdout)["passed"], 1)


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
    assert_eq(metadata["Requires-Python"], ">=3.15")
    assert_in("anyio", metadata.get_all("Provides-Extra"))
    project_urls = metadata.get_all("Project-URL")
    assert_in("Repository, https://github.com/crpier/snektest", project_urls)
    assert_in("Issues, https://github.com/crpier/snektest/issues", project_urls)
    classifiers = metadata.get_all("Classifier")
    for classifier in (
        "Operating System :: MacOS",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.15",
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
    _assert_standalone_cli_startup(wheel, target=tmp_dir / "wheel-cli", cwd=tmp_dir)
    _assert_standalone_cli_startup(sdist, target=tmp_dir / "sdist-cli", cwd=tmp_dir)
