"""Release-gate command and GitHub policy tests."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from snektest import assert_eq, assert_false, assert_ne, assert_true, test

_RELEASE_STAGES = [
    "lock",
    "format",
    "lint",
    "types",
    "dependency-audit",
    "tests-with-coverage",
    "coverage-threshold",
]


@test(mark="fast")
def test_every_test_lives_in_the_canonical_suite() -> None:
    """The release command must not silently omit a root-level scratch test."""
    assert_false(Path("test_test.py").exists())
    assert_true(Path("tests/isolated/test_fixture_typing.py").is_file())


@test(mark="medium")
def test_release_gate_lists_every_enforced_stage() -> None:
    """Contributors can inspect what the single release-health command runs."""
    listed = subprocess.run(
        [sys.executable, "scripts/release_check.py", "--list"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert_eq(listed.returncode, 0, msg=listed.stderr)
    assert_eq(listed.stdout.splitlines(), _RELEASE_STAGES)


@test(mark="fast")
def test_release_gate_uses_only_declared_tools() -> None:
    """The gate pins uv, declares its auditor, and does not retain Pyright."""
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    development_dependencies = pyproject["dependency-groups"]["dev"]
    assert_eq(pyproject["tool"]["uv"]["required-version"], "==0.9.26")
    assert_true(
        any(
            dependency.startswith("pip-audit")
            for dependency in development_dependencies
        )
    )
    assert_false(
        any(dependency.startswith("pyright") for dependency in development_dependencies)
    )


@test(mark="fast")
def test_locked_pygments_version_has_the_reviewed_advisory_fix() -> None:
    """The production dependency graph contains reviewed Pygments 2.20.0."""
    with Path("uv.lock").open("rb") as lock_file:
        lock = tomllib.load(lock_file)

    locked_packages = {
        package["name"]: package.get("version") for package in lock["package"]
    }
    assert_false("pyright" in locked_packages)
    assert_eq(locked_packages["pygments"], "2.20.0")


@test(mark="fast")
def test_coverage_policy_measures_the_core_package_at_an_honest_floor() -> None:
    """Bundled examples do not dilute the enforced core-library measurement."""
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    coverage_run = pyproject["tool"]["coverage"]["run"]
    coverage_report = pyproject["tool"]["coverage"]["report"]
    assert_eq(coverage_run["source"], ["snektest"])
    assert_eq(coverage_run["omit"], ["*/snektest/examples/*"])
    assert_eq(coverage_report["fail_under"], 90)


@test(mark="fast")
def test_pull_requests_run_the_same_bounded_cross_platform_gate() -> None:
    """GitHub calls the local command on every declared supported platform."""
    workflow = Path(".github/workflows/quality.yml").read_text()

    assert_true("pull_request:" in workflow)
    assert_true("push:" in workflow)
    assert_true("workflow_call:" in workflow)
    assert_true("ubuntu-latest" in workflow)
    assert_true("macos-latest" in workflow)
    assert_true("windows-latest" in workflow)
    assert_true('python-version: "3.14"' in workflow)
    assert_true('PYTHONUTF8: "1"' in workflow)
    assert_true("timeout-minutes: 15" in workflow)
    assert_true("uv run --locked python scripts/release_check.py" in workflow)
    action_references = re.findall(r"uses: [^\s]+@([^\s]+)", workflow)
    assert_ne(action_references, [])
    assert_true(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_references))


@test(mark="fast")
def test_tag_release_requires_the_gate_before_trusted_publication() -> None:
    """Only a reviewed main commit with the exact package tag reaches PyPI."""
    workflow = Path(".github/workflows/release.yml").read_text()

    assert_true("tags:" in workflow)
    assert_true("uses: $/.github/workflows/quality.yml" in workflow)
    assert_true("needs: gate" in workflow)
    assert_true("git merge-base --is-ancestor" in workflow)
    assert_true("commits/$GITHUB_SHA/pulls" in workflow)
    assert_true("snektest.__version__" in workflow)
    assert_true("environment: pypi" in workflow)
    assert_true("id-token: write" in workflow)
    assert_true("attestations: true" in workflow)
    assert_false("password:" in workflow)
    action_references = re.findall(r"uses: [^\s]+@([^\s]+)", workflow)
    assert_ne(action_references, [])
    assert_true(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_references))


@test(mark="fast")
def test_contributor_commands_document_one_release_gate() -> None:
    """Contributor docs and helpers point to the command GitHub runs."""
    command = "uv run --locked python scripts/release_check.py"
    readme = Path("README.md").read_text()
    contributor_guide = Path("AGENTS.md").read_text()
    helper_commands = Path("justfile").read_text()

    assert_true(command in readme)
    assert_true(command in contributor_guide)
    assert_true(command in helper_commands)
    assert_false("pyright" in contributor_guide.casefold())
    assert_false("pyright" in helper_commands.casefold())
