"""Compatibility tests for the public interface shipped in the wheel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from textwrap import dedent

from snektest import (
    assert_eq,
    assert_false,
    assert_in,
    assert_true,
    load_fixture,
    test,
)
from testutils.fixtures import tmp_dir_fixture

_TIMEOUT_SECONDS = 60


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


@test(mark="slow")
def test_built_wheel_exposes_runtime_and_static_public_interface() -> None:
    """A consumer can import and type-check the supported wheel interface."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    repository = Path.cwd()
    distribution_dir = tmp_dir / "dist"
    target_dir = tmp_dir / "installed"

    built = _run(
        ["uv", "build", "--wheel", "--out-dir", str(distribution_dir)],
        cwd=repository,
    )
    assert_eq(built.returncode, 0, msg=built.stderr)
    wheels = list(distribution_dir.glob("*.whl"))
    assert_eq(len(wheels), 1)
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
    assert_true("snektest/__init__.pyi" in members)
    assert_true("snektest/py.typed" in members)

    installed = _run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(target_dir),
            "--no-deps",
            str(wheel),
        ],
        cwd=tmp_dir,
    )
    assert_eq(installed.returncode, 0, msg=installed.stderr)

    consumer = tmp_dir / "consumer.py"
    consumer.write_text(
        dedent("""
            from collections.abc import Generator
            import json

            from hypothesis import strategies as st

            import snektest
            from snektest import (
                Param,
                Scope,
                SnektestError,
                fixture,
                skip,
                test,
                test_hypothesis,
                xfail,
            )

            @fixture(scope=Scope.SESSION)
            def value() -> Generator[int]:
                yield 1

            @test(
                [Param(value=1, name="one")],
                [Param(value="two", name="two")],
            )
            def parameter_types(first: int, second: str) -> None:
                pass

            @test(xfail="known defect")
            def expected_outcome() -> None:
                xfail("known defect")

            @test()
            def conditional_outcome() -> None:
                skip("optional dependency unavailable")

            @test_hypothesis(
                st.integers(),
                st.text(),
                st.booleans(),
                st.binary(),
                st.floats(),
            )
            def strategy_types(
                first: int,
                second: str,
                third: bool,
                fourth: bytes,
                fifth: float,
            ) -> None:
                pass

            scope: Scope = value().scope
            public_error: type[SnektestError] = snektest.FixtureError
            print(json.dumps({
                "all": snektest.__all__,
                "error": public_error.__name__,
                "scope": scope.value,
            }))
        """),
    )
    execute_consumer = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(target_dir)!r}); "
        f"runpy.run_path({str(consumer)!r}, run_name='__main__')"
    )
    executed = _run(
        [sys.executable, "-I", "-c", execute_consumer],
        cwd=tmp_dir,
    )
    assert_eq(executed.returncode, 0, msg=executed.stderr)
    payload = json.loads(executed.stdout)
    assert_eq(payload["scope"], "session")
    assert_eq(payload["error"], "FixtureError")
    assert_true("SnektestError" in payload["all"])
    assert_true("skip" in payload["all"])
    assert_true("xfail" in payload["all"])
    assert_false("UnreachableError" in payload["all"])

    type_checker = Path(sys.executable).with_name("ty")
    checked = _run(
        [
            str(type_checker),
            "check",
            "--python",
            sys.executable,
            "--extra-search-path",
            str(target_dir),
            str(consumer),
        ],
        cwd=tmp_dir,
    )
    assert_eq(checked.returncode, 0, msg=checked.stdout + checked.stderr)

    invalid_consumer = tmp_dir / "invalid_consumer.py"
    invalid_consumer.write_text(
        dedent("""
            from collections.abc import Generator

            from hypothesis import strategies as st

            from snektest import Param, fixture, test, test_hypothesis

            @fixture(scope="invalid")
            def invalid_scope() -> Generator[int]:
                yield 1

            @test([Param(value=1, name="one")])
            def invalid_parameter(value: str) -> None:
                pass

            @test_hypothesis(st.integers())
            def invalid_strategy(value: str) -> None:
                pass
        """),
    )
    rejected = _run(
        [
            str(type_checker),
            "check",
            "--python",
            sys.executable,
            "--extra-search-path",
            str(target_dir),
            str(invalid_consumer),
        ],
        cwd=tmp_dir,
    )
    diagnostics = rejected.stdout + rejected.stderr
    assert_eq(rejected.returncode, 1, msg=diagnostics)
    assert_in("no-matching-overload", diagnostics)
    assert_in("invalid-argument-type", diagnostics)
