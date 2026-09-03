"""Regressions for published runtime and operating-system support."""

import tomllib
from pathlib import Path

from snektest import assert_eq, assert_in, assert_not_in, assert_true, test


@test(mark="fast")
def test_python_requirement_precedes_install_command() -> None:
    """Readers see the minimum Python version before attempting installation."""
    readme = Path("README.md").read_text()

    assert_true(
        readme.index("Snektest requires Python 3.14 or newer.")
        < readme.index("uv add snektest")
    )


@test(mark="fast")
def test_documented_support_matches_release_configuration() -> None:
    """The compatibility table names only the runtime and OS matrix we verify."""
    readme = Path("README.md").read_text()
    workflow = Path(".github/workflows/quality.yml").read_text()
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert_in(
        "| CPython 3.14 (GIL-enabled) | Supported | Supported | Supported |", readme
    )
    assert_eq(pyproject["project"]["requires-python"], ">=3.14")
    classifiers = pyproject["project"]["classifiers"]
    assert_not_in("Operating System :: OS Independent", classifiers)
    for classifier in (
        "Operating System :: MacOS",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.14",
        "Programming Language :: Python :: Implementation :: CPython",
    ):
        assert_in(classifier, classifiers)
    assert_in('python-version: "3.14"', workflow)
    for operating_system in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert_in(operating_system, workflow)


@test(mark="fast")
def test_free_threaded_python_is_explicitly_unsupported() -> None:
    """The table does not imply support for an untested interpreter build."""
    readme = Path("README.md").read_text()

    assert_in(
        "| CPython 3.14 (free-threaded) | Not supported | Not supported | Not supported |",
        readme,
    )
