"""Exception-message assertions retain normal CLI result classification."""

import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "1"), name="worker"),
    ],
    mark="slow",
)
def test_message_matching_reports_failures(
    arguments: tuple[str, ...],
) -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import assert_raises, test

            class BrokenMessageError(ValueError):
                def __str__(self) -> str:
                    raise RuntimeError("broken formatter")

            @test(mark="fast")
            def test_mismatch() -> None:
                with assert_raises(ValueError, match="wanted"):
                    raise ValueError("actual message")

            @test(mark="fast")
            def test_broken_formatter() -> None:
                with assert_raises(ValueError, match=""):
                    raise BrokenMessageError("original exception")

            @test(mark="fast")
            def test_later_case() -> None:
                with assert_raises(ValueError, match="valid"):
                    raise ValueError("valid message")
        """),
    )

    document = run_test_subprocess(test_file, *arguments, timeout=15)
    assert_eq(document["returncode"], 1)
    assert_eq(document["failed"], 2)
    assert_eq(document["errors"], 0)
    assert_eq(document["passed"], 1)
    assert_eq(document["stderr"], "")
    assert_eq(
        [case["status"] for case in document["tests"]],
        ["failed", "failed", "passed"],
    )
    mismatch, broken, _ = document["tests"]
    assert_eq(mismatch["exception"]["type"], "AssertionFailure")
    assert_in("actual message", mismatch["exception"]["message"])
    assert_in("wanted", mismatch["exception"]["message"])
    assert_eq(mismatch["exception"]["cause"]["type"], "ValueError")
    assert_eq(broken["exception"]["type"], "AssertionFailure")
    assert_in("str() raised RuntimeError", broken["exception"]["message"])
    assert_eq(broken["exception"]["cause"]["type"], "BrokenMessageError")

    console = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(test_file)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert_eq(console.returncode, 1)
    assert_in("2 failed", console.stdout)
    assert_in("1 passed", console.stdout)
    assert_in("actual message", console.stdout)
    assert_in("wanted", console.stdout)
    assert_in("str() raised RuntimeError", console.stdout)
