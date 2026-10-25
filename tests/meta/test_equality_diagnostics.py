"""Equality explanations survive the public CLI and worker protocols."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_hostile_operands_remain_a_json_assertion_failure(workers: int | None) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_eq, test

        class HostileDict(dict[str, int]):
            def __ne__(self, other: object) -> bool:
                return True
            def __len__(self) -> int:
                raise RuntimeError("must not inspect subclass")
            def __repr__(self) -> str:
                raise RuntimeError("must not render subclass")

        @test(mark="fast")
        def mismatch() -> None:
            assert_eq(HostileDict(), {})
    """),
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments)

    assert_eq(result.get("failed"), 1, msg=result["stdout"])
    assert_eq(result["errors"], 0)
    assert_eq(result["schema_version"], 1)
    assert_in("<HostileDict>", result["tests"][0]["exception"]["message"])


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_console_and_json_keep_the_same_useful_equality_details(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_eq, test

        @test(mark="fast")
        def sql_mismatch() -> None:
            prefix = "SELECT " + "column, " * 49
            assert_eq(prefix + "id FROM users", prefix + "key FROM users")

        @test(mark="fast")
        def report_mismatch() -> None:
            assert_eq({"types": ["int", "str"]}, {"types": ["int", "bytes"]})

        @test(mark="fast")
        def custom_message() -> None:
            assert_eq([1], [2], msg="application-specific failure")
    """),
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    document = run_test_subprocess(path, *arguments)
    console = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert_eq(document["failed"], 3)
    assert_eq(document["errors"], 0)
    assert_eq(document["schema_version"], 1)
    assert_eq(console.returncode, 1)
    for index, fragment in enumerate(
        (
            "First difference at character 399 (0-based)",
            "At $['types'][1]: 'str' != 'bytes'",
            "application-specific failure",
        )
    ):
        assert_in(fragment, document["tests"][index]["exception"]["message"])
        assert_in(fragment, console.stdout)
    assert_eq(
        document["tests"][2]["exception"]["message"], "application-specific failure"
    )


@test([Param("normal", name="normal"), Param("all", name="all")], mark="slow")
def test_passing_equality_does_not_load_the_failure_formatter(mode: str) -> None:
    source = (
        "import sys; "
        f"sys.path.insert(0, {str(Path.cwd())!r}); "
        "from snektest import assert_eq; "
        "assert_eq([1, 2], [1, 2]); "
        "print('snektest.equality' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-X", f"lazy_imports={mode}", "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert_eq(completed.returncode, 0, msg=completed.stderr)
    assert_eq(completed.stdout, "False\n")
