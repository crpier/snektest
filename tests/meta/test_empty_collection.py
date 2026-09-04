"""CLI regressions for empty and incomplete test collection."""

import json
import subprocess
import sys
from textwrap import dedent
from typing import Any, cast

from snektest import assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


def _run_selection(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "snektest.cli", *args, path],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_json_selection(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return _run_selection(path, "--json-output", *args)


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(result.stdout))


def _assert_rejected_empty_selection(result: subprocess.CompletedProcess[str]) -> None:
    rejection = _json_output(result)

    assert_eq(result.returncode, 2)
    assert_eq(rejection["error"]["type"], "EmptyCollectionError")
    assert_in("No tests selected", rejection["error"]["message"])


def _assert_allowed_empty_selection(result: subprocess.CompletedProcess[str]) -> None:
    summary = _json_output(result)

    assert_eq(result.returncode, 0)
    assert_eq(summary["tests"], [])
    assert_eq(summary["passed"], 0)


@test(mark="slow")
def test_empty_directory_requires_explicit_allow_empty() -> None:
    """Zero selected tests fail unless the command explicitly permits them."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    empty_dir = tmp_dir / "empty-directory"
    empty_dir.mkdir()

    _assert_rejected_empty_selection(_run_json_selection(str(empty_dir)))
    _assert_rejected_empty_selection(
        _run_json_selection(str(empty_dir), "--workers", "1")
    )
    _assert_allowed_empty_selection(
        _run_json_selection(str(empty_dir), "--allow-empty")
    )
    _assert_allowed_empty_selection(
        _run_json_selection(str(empty_dir), "--allow-empty", "--workers", "1")
    )


@test(mark="slow")
def test_file_without_tests_requires_explicit_allow_empty() -> None:
    """An explicit Python test file cannot silently contribute no tests."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(tmp_dir, "VALUE = 1\n", name="test_empty")

    _assert_rejected_empty_selection(_run_json_selection(str(test_file)))
    _assert_allowed_empty_selection(
        _run_json_selection(str(test_file), "--allow-empty")
    )


@test(mark="slow")
def test_empty_filter_cannot_hide_beside_valid_filter() -> None:
    """Every requested filter must contribute unless empty selection is allowed."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    empty_file = create_test_file(tmp_dir, "VALUE = 1\n", name="test_empty_filter")
    valid_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test()
            def test_valid() -> None:
                pass
        """),
        name="test_valid_filter",
    )

    rejected = _run_json_selection(str(valid_file), str(empty_file))
    allowed = _run_json_selection(str(valid_file), str(empty_file), "--allow-empty")

    _assert_rejected_empty_selection(rejected)
    allowed_summary = _json_output(allowed)
    assert_eq(allowed.returncode, 0)
    assert_eq(len(allowed_summary["tests"]), 1)


@test(mark="slow")
def test_marker_without_matches_requires_explicit_allow_empty() -> None:
    """A marker typo or mismatch is a failed selection by default."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test(mark="fast")
            def test_fast() -> None:
                pass
        """),
        name="test_marker",
    )

    _assert_rejected_empty_selection(
        _run_json_selection(str(test_file), "--mark", "slow")
    )
    _assert_allowed_empty_selection(
        _run_json_selection(str(test_file), "--mark", "slow", "--allow-empty")
    )


@test(mark="slow")
def test_human_output_explains_empty_selection() -> None:
    """The normal console reports why no success summary was produced."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    empty_dir = tmp_dir / "human-empty"
    empty_dir.mkdir()

    result = _run_selection(str(empty_dir))

    assert_eq(result.returncode, 2)
    assert_in("Collection error: No tests selected", result.stdout)


@test(mark="slow")
def test_bare_test_decorator_is_a_clear_collection_error() -> None:
    """Common `@test` misuse fails instead of silently removing the function."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test
            def test_bare() -> None:
                pass
        """),
        name="test_bare_decorator",
    )

    result = _run_json_selection(str(test_file))
    error = _json_output(result)["error"]

    assert_eq(result.returncode, 2)
    assert_eq(error["type"], "InvalidTestDefinitionError")
    assert_in("Use @test()", error["message"])


@test(mark="slow")
def test_empty_parameter_axis_cannot_drop_one_test_from_nonempty_file() -> None:
    """An empty parameter axis fails even when another test keeps the plan nonempty."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test

            @test([])
            def test_dropped(value: int) -> None:
                _ = value

            @test()
            def test_survivor() -> None:
                pass
        """),
        name="test_empty_axis",
    )

    result = _run_json_selection(str(test_file))
    error = _json_output(result)["error"]

    assert_eq(result.returncode, 2)
    assert_in("parameter list", error["message"])
    assert_in("must not be empty", error["message"])
