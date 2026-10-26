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

    consumer = tmp_dir / "test_consumer.py"
    consumer.write_text(
        dedent("""
            from collections.abc import Generator
            import json
            import logging
            import re
            from typing import assert_type
            from pathlib import Path

            from hypothesis import strategies as st

            import snektest
            from snektest import (
                LogCapture,
                Param,
                capture_logs,
                Scope,
                SnektestError,
                assert_raises,
                fixture,
                skip,
                test,
                test_hypothesis,
                temporary_directory,
                xfail,
            )
            from snektest.cli import run_tests_programmatic
            from snektest.junit import build_junit_xml
            from snektest.models import FilterItem, RunResult
            from snektest.structured import SUPPORTED_SCHEMA_VERSIONS

            async def structured_result(path: str) -> str:
                completed_run: RunResult = await run_tests_programmatic(
                    [FilterItem(path)]
                )
                return build_junit_xml(completed_run)

            @fixture(scope=Scope.SESSION)
            def value() -> Generator[int]:
                yield 1

            @test(
                [Param(value=1, name="one")],
                [Param(value="two", name="two")],
            )
            def parameter_types(first: int, second: str) -> None:
                pass

            @test()
            def owned_path() -> None:
                root = temporary_directory(base=Path.cwd(), retention="never")
                assert_type(root, Path)

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

            with capture_logs("installed.consumer", level=logging.INFO) as captured:
                assert_type(captured, LogCapture)
                logging.getLogger("installed.consumer").info("installed capture")
            assert_type(captured.records, tuple[logging.LogRecord, ...])
            assert_type(captured.messages, tuple[str, ...])

            class FieldError(ValueError):
                code: int = 7

            with assert_raises(FieldError, match=re.compile("field", re.IGNORECASE)) as caught:
                raise FieldError("FIELD")
            assert_type(caught.exception, FieldError)
            code: int = caught.exception.code

            with assert_raises(ValueError, TypeError, match="message") as multiple:
                raise TypeError("message")
            assert_type(multiple.exception, ValueError | TypeError)

            scope: Scope = value().scope
            public_error: type[SnektestError] = snektest.FixtureError
            print(json.dumps({
                "all": snektest.__all__,
                "error": public_error.__name__,
                "scope": scope.value,
                "exception_code": code,
                "schemas": SUPPORTED_SCHEMA_VERSIONS,
                "messages": captured.messages,
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
    assert_eq((payload["scope"], payload["exception_code"]), ("session", 7))
    assert_eq(payload["error"], "FixtureError")
    assert_eq(payload["schemas"], [1])
    assert_eq(payload["messages"], ["installed capture"])
    assert_true(
        set(payload["all"])
        >= {
            "LogCapture",
            "capture_logs",
            "temporary_directory",
            "SnektestError",
            "skip",
            "xfail",
        }
    )
    assert_false("UnreachableError" in payload["all"])

    installed_project = tmp_dir / "installed-project"
    installed_tests = installed_project / "checks"
    installed_tests.mkdir(parents=True)
    _ = (installed_project / "pyproject.toml").write_text(
        '[tool.snektest]\ntest_paths = ["checks"]\n',
        encoding="utf-8",
    )
    installed_test = installed_tests / "test_installed_collection.py"
    installed_test.write_text(
        dedent("""
            from snektest import test

            @test()
            def test_installed_case() -> None:
                raise RuntimeError("collect-only executed the body")
        """),
        encoding="utf-8",
    )
    collect_only = _run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(target_dir)!r}); "
                "from snektest.cli import main; "
                "sys.argv = ['snektest', '--collect-only']; "
                "main()"
            ),
        ],
        cwd=installed_project,
    )
    assert_eq(collect_only.returncode, 0, msg=collect_only.stderr)
    installed_selector = (
        f"{Path('checks') / 'test_installed_collection.py'}::test_installed_case"
    )
    assert_in(installed_selector, collect_only.stdout)

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

    executed_owned_test = _run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import asyncio, runpy, sys; "
                f"sys.path.insert(0, {str(target_dir)!r}); "
                f"namespace = runpy.run_path({str(consumer)!r}); "
                "from snektest.cli import run_tests_programmatic; "
                "from snektest.models import FilterItem; "
                f"result = asyncio.run(run_tests_programmatic([FilterItem({str(consumer) + '::owned_path'!r})])); "
                "sys.exit(result.exit_code)"
            ),
        ],
        cwd=tmp_dir,
    )
    assert_eq(
        executed_owned_test.returncode,
        0,
        msg=executed_owned_test.stdout + executed_owned_test.stderr,
    )

    _check_invalid_authoring_types(type_checker, target_dir, tmp_dir)
    _check_invalid_match_types(type_checker, target_dir, tmp_dir)


def _check_invalid_authoring_types(
    type_checker: Path, target_dir: Path, tmp_dir: Path
) -> None:
    """Reject invalid fixture, decorator, log and directory arguments in the wheel."""
    invalid_consumer = tmp_dir / "invalid_consumer.py"
    invalid_consumer.write_text(
        dedent("""
            from collections.abc import Generator

            from hypothesis import strategies as st

            from snektest import Param, capture_logs, fixture, temporary_directory, test, test_hypothesis

            capture_logs(42)
            capture_logs("consumer", level="DEBUG")
            temporary_directory(base=42)
            temporary_directory(retention="typo")

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
    assert_in("capture_logs", diagnostics)
    assert_in("temporary_directory", diagnostics)


def _check_invalid_match_types(
    type_checker: Path,
    target_dir: Path,
    tmp_dir: Path,
) -> None:
    """Verify invalid match arguments against the installed wheel signature."""
    invalid_match_consumer = tmp_dir / "invalid_match_consumer.py"
    invalid_match_consumer.write_text(
        dedent("""
            import re

            from snektest import assert_raises

            _ = assert_raises(ValueError, match=b"bytes")
            _ = assert_raises(ValueError, match=re.compile(b"bytes"))
            _ = assert_raises(ValueError, match=42)
        """),
        encoding="utf-8",
    )
    rejected_match = _run(
        [
            str(type_checker),
            "check",
            "--python",
            sys.executable,
            "--extra-search-path",
            str(target_dir),
            str(invalid_match_consumer),
        ],
        cwd=tmp_dir,
    )
    match_diagnostics = rejected_match.stdout + rejected_match.stderr
    assert_eq(rejected_match.returncode, 1, msg=match_diagnostics)
    assert_eq(match_diagnostics.count("error[invalid-argument-type]"), 3)
