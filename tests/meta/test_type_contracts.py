"""Python-native static contracts through real runner and checker interfaces."""

import json
import os
import subprocess
import sys
from textwrap import dedent
from xml.etree import ElementTree

from snektest import Param, assert_eq, assert_in, load_fixture, test
from snektest.cli import run_tests_programmatic
from snektest.junit import build_junit_xml
from snektest.models import FilterItem
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_python_function_checks_positive_and_negative_assertions_without_execution(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import assert_type_error, test_typing

        def must_not_execute() -> None:
            raise RuntimeError("static test body executed")

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer(symbolic: int) -> None:
            must_not_execute()
            assert_type(takes_int(symbolic), int)
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_python_contract",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, *arguments, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["failed"], 0)


@test(mark="slow")
def test_broken_shared_positive_control_cannot_certify_a_negative_assertion() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return "broken positive control"

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_broken_typing_control",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)


@test([Param(None, name="local"), Param(2, name="workers")], mark="slow")
def test_keyword_selection_does_not_turn_unselected_contracts_into_failures(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def chosen() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("first")

        @test_typing
        def unselected() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("second")
        """),
        name="test_typing_selection",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(path, "-k", "chosen", *arguments, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    assert_eq(result["selected_tests"], 1)


@test(mark="slow")
def test_async_static_function_checks_awaited_types_without_running_coroutines() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import assert_type_error, test_typing

        async def value(argument: int) -> int:
            raise RuntimeError("static coroutine executed")

        @test_typing
        async def consumer() -> None:
            assert_type(await value(1), int)
            with assert_type_error("invalid-argument-type"):
                await value("wrong")
        """),
        name="test_async_typing",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_static_analysis_preserves_package_relative_imports() -> None:
    directory = load_fixture(tmp_dir_fixture()) / "typing_package"
    directory.mkdir(exist_ok=True)
    (directory / "__init__.py").write_text("")
    (directory / "helpers.py").write_text(
        "def takes_int(value: int) -> int:\n    return value\n"
    )
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import assert_type_error, test_typing
        from .helpers import takes_int

        @test_typing
        def consumer() -> None:
            assert_type(takes_int(1), int)
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_package_typing",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(
    [
        Param(
            (
                "from snektest import assert_type_error as reject, test_typing as contract",
                "contract",
                "reject",
            ),
            name="aliases",
        ),
        Param(
            ("import snektest as s", "s.test_typing", "s.assert_type_error"),
            name="qualified",
        ),
    ],
    mark="slow",
)
def test_static_assertions_support_normal_python_import_styles(
    imports: tuple[str, str, str],
) -> None:
    statement, decorator, assertion = imports
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        {statement}

        def takes_int(value: int) -> int:
            return value

        @{decorator}
        def consumer() -> None:
            with {assertion}("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_typing_aliases",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_broken_import_never_counts_as_a_successful_negative_assertion() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        @test_typing
        def consumer() -> None:
            with assert_type_error("unresolved-import"):
                import nonexistent_snektest_contract_dependency
        """),
        name="test_typing_missing_import",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["errors"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["failed"], 0)
    assert_eq(result["passed"], 0)


@test(
    [
        Param(("takes_int(1)", "invalid-argument-type"), name="missing-diagnostic"),
        Param(("takes_int('wrong')", "invalid-return-type"), name="wrong-rule"),
        Param(
            ("takes_int('wrong'); takes_int('also wrong')", "invalid-argument-type"),
            name="extra-diagnostic",
        ),
    ],
    mark="slow",
)
def test_negative_assertions_require_exactly_the_expected_diagnostic(
    case: tuple[str, str],
) -> None:
    operation, rule = case
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            with assert_type_error("{rule}"):
                {operation}
        """),
        name="test_typing_mismatch",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)
    assert_eq(result["tests"][0]["exception"]["type"], "AssertionFailure")


@test(mark="slow")
def test_nested_assertions_each_own_exactly_their_diagnostic() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("outer")
                with assert_type_error("invalid-argument-type"):
                    takes_int("inner")
        """),
        name="test_typing_nested",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_positive_only_contract_is_checked_even_though_normal_ty_skips_its_body() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import test_typing

        @test_typing
        def consumer(symbolic: int) -> None:
            assert_type(symbolic, str)
        """),
        name="test_typing_positive_only",
    )
    normal = subprocess.run(
        [sys.executable, "-m", "ty", "check", "--error", "all", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(normal.returncode, 0, msg=normal.stdout + normal.stderr)
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)
    assert_in("type-assertion-failure", result["tests"][0]["exception"]["message"])
    assert_in(f"{path}:7:", result["tests"][0]["exception"]["message"])


@test(mark="slow")
def test_symbolic_parameter_cannot_impersonate_the_global_assertion_marker() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import Callable
        from contextlib import AbstractContextManager
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer(
            assert_type_error: Callable[[str], AbstractContextManager[None]],
        ) -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_shadowed_typing_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)


@test(mark="slow")
def test_nested_function_parameter_cannot_impersonate_the_assertion_marker() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import Callable
        from contextlib import AbstractContextManager
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            def nested(
                assert_type_error: Callable[[str], AbstractContextManager[None]],
            ) -> None:
                with assert_type_error("invalid-argument-type"):
                    takes_int("wrong")
        """),
        name="test_nested_shadowed_typing_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)


@test(mark="slow")
def test_assertion_imported_inside_the_static_body_is_recognized_without_execution() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            from snektest import assert_type_error as reject
            with reject("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_local_typing_import",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_error_in_assertion_header_cannot_certify_its_healthy_body() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import test_typing

        @test_typing
        def consumer(condition: bool) -> None:
            if condition:
                from snektest import assert_type_error as reject
            with reject("possibly-unresolved-reference"):
                pass
        """),
        name="test_typing_header_error",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)


@test(mark="slow")
def test_mutable_function_name_cannot_redirect_analysis_to_a_different_body() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def decoy() -> None:
            pass

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int(1)

        setattr(getattr(consumer, "__wrapped__"), "__name__", "decoy")
        """),
        name="test_typing_source_identity",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["failed"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq(result["errors"], 0)
    assert_in("no diagnostics", result["tests"][0]["exception"]["message"])


@test(mark="slow")
def test_module_import_inside_the_static_body_supports_qualified_assertions() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            import snektest as s
            with s.assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_local_qualified_typing_import",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_oversized_native_report_is_an_error_and_releases_temporary_resources() -> None:
    directory = load_fixture(tmp_dir_fixture())
    scratch = directory / "report-temporary-files"
    scratch.mkdir()
    source = (
        "from snektest import test, test_typing\n"
        "def takes_int(value: int) -> int:\n    return value\n"
        "@test_typing\ndef consumer() -> None:\n"
        + '    takes_int("wrong")\n' * 10_000
        + "@test(mark='fast')\ndef following_runtime_case() -> None:\n    pass\n"
    )
    path = create_test_file(directory, source, name="test_oversized_ty_report")
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", "--json-output", str(path)],
        env=dict(os.environ, TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_in("4 MiB report limit", result["tests"][0]["exception"]["message"])
    assert_eq(list(scratch.iterdir()), [])


@test(mark="slow")
def test_source_changed_after_collection_cannot_certify_the_original_contract() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from pathlib import Path
        from snektest import assert_type_error, test, test_typing

        def takes_int(value: int) -> int:
            return value

        @test(mark="medium")
        def change_source() -> None:
            path = Path(__file__)
            path.write_text(path.read_text().replace(
                "    " + "takes_int(1)", '    takes_int("wrong")',
            ))

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int(1)
        """),
        name="test_typing_changed_source",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_in("source changed", result["tests"][1]["exception"]["message"])


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
async def test_static_contracts_preserve_programmatic_fail_fast_and_junit_results(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import assert_type_error, test, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def positive(symbolic: int) -> None:
            assert_type(takes_int(symbolic), int)

        @test_typing
        def missing_rejection() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int(1)

        @test(mark="fast")
        def must_not_start() -> None:
            raise RuntimeError("fail-fast started a later body")
        """),
        name="test_typing_programmatic_fail_fast",
    )
    run = await run_tests_programmatic(
        [FilterItem(str(path))], workers=workers, fail_fast=True, timeout=10
    )
    assert_eq((run.selected_tests, run.total_tests, run.stopped_early), (3, 2, True))
    assert_eq((run.passed, run.failed, run.errors, run.exit_code), (1, 1, 0, 1))
    assert_eq([case.markers for case in run.test_results], [("slow",), ("slow",)])
    suite = ElementTree.fromstring(build_junit_xml(run))  # noqa: S314 -- trusted adapter output
    assert_eq(
        (suite.get("tests"), suite.get("failures"), suite.get("errors")),
        ("2", "1", "0"),
    )
    failures = suite.findall("testcase/failure")
    assert_eq(len(failures), 1)
    assert_in("no diagnostics", failures[0].get("message", ""))


@test(mark="slow")
def test_replacement_worker_can_run_a_static_contract_after_a_process_exit() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import os
        from snektest import assert_type_error, test, test_typing

        def takes_int(value: int) -> int:
            return value

        @test(mark="slow")
        def crash_worker() -> None:
            os._exit(23)

        @test_typing
        def after_replacement() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_typing_replacement",
    )
    result = run_test_subprocess(path, "--workers", "1", timeout=30)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_eq(result["tests"][1]["status"], "passed")


@test(mark="slow")
def test_single_line_assertion_uses_python_lines_and_unicode_columns() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            text = "a\u2028b"
            with assert_type_error("invalid-argument-type") as éééééééééééééééééééé: takes_int("wrong")
        """),
        name="test_typing_unicode",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)


@test(mark="slow")
def test_runtime_rebinding_cannot_erase_a_source_declared_static_assertion() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from contextlib import nullcontext
        from snektest import assert_type_error, test, test_typing

        def takes_int(value: int) -> int:
            return value

        @test(mark="fast")
        def rebind_runtime_name() -> None:
            globals()["assert_type_error"] = nullcontext

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int(1)
        """),
        name="test_typing_rebound_globals",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq((result["errors"], result["failed"], result["passed"]), (0, 1, 1))
    assert_in("no diagnostics", result["tests"][1]["exception"]["message"])


@test(mark="slow")
def test_parent_cancellation_during_native_startup_drains_tasks_and_temporary_files() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture())
    scratch = directory / "cancelled-typing-temporary-files"
    scratch.mkdir()
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                takes_int("wrong")
        """),
        name="test_cancelled_ty_startup",
    )
    driver = directory / "cancel_typing_driver.py"
    driver.write_text(
        dedent("""
        import asyncio
        from pathlib import Path
        import sys
        from snektest import assert_eq, assert_false, assert_true, fail
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem

        requested = False

        def audit(event: str, arguments: tuple[object, ...]) -> None:
            global requested
            if event == "subprocess.Popen" and Path(str(arguments[0])).name in ("ty", "ty.exe"):
                task = asyncio.current_task()
                if task is not None:
                    requested = True
                    asyncio.get_running_loop().call_soon(task.cancel)

        async def main() -> None:
            unrelated = asyncio.create_task(asyncio.sleep(60))
            baseline = set(asyncio.all_tasks())
            sys.addaudithook(audit)
            run = asyncio.create_task(run_tests_programmatic([FilterItem(sys.argv[1])]))
            try:
                try:
                    await run
                except asyncio.CancelledError:
                    pass
                else:
                    fail("parent cancellation did not propagate")
                assert_true(requested)
                assert_false(unrelated.done())
                assert_eq([task.get_name() for task in asyncio.all_tasks() - baseline], [])
            finally:
                unrelated.cancel()
                await asyncio.gather(unrelated, return_exceptions=True)
            print("cancelled cleanly")

        asyncio.run(main())
        """),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(driver), str(path)],
        env=dict(os.environ, TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("cancelled cleanly", completed.stdout)
    assert_eq(list(scratch.iterdir()), [])


@test(mark="slow")
def test_copied_assertion_marker_cannot_silently_erase_an_expectation() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from snektest import assert_type_error, test_typing
        reject = assert_type_error

        def takes_int(value: int) -> int:
            return value

        @test_typing
        def consumer() -> None:
            with reject("invalid-argument-type"):
                takes_int(1)
        """),
        name="test_copied_static_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 2, msg=result["stdout"] + result["stderr"])
    assert_in("direct Snektest import", result["error"]["message"])


@test(mark="slow")
def test_rebinding_an_imported_marker_is_rejected_as_an_invalid_declaration() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from contextlib import AbstractContextManager, nullcontext
        from snektest import assert_type_error, test_typing

        def replacement(rule: str) -> AbstractContextManager[None]:
            return nullcontext()

        assert_type_error = replacement

        @test_typing
        def consumer() -> None:
            with assert_type_error("invalid-argument-type"):
                pass
        """),
        name="test_rebound_static_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 2, msg=result["stdout"] + result["stderr"])
    assert_in("copied or indirect marker values", result["error"]["message"])


@test(mark="slow")
def test_dynamic_marker_lookup_cannot_silently_erase_an_expectation() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import snektest as s
        from snektest import test_typing

        @test_typing
        def consumer() -> None:
            with getattr(s, "assert_type_error")("invalid-argument-type"):
                pass
        """),
        name="test_dynamic_static_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 2, msg=result["stdout"] + result["stderr"])
    assert_in("dynamic marker lookup", result["error"]["message"])


@test(mark="slow")
def test_opaque_decorator_cannot_disable_a_positive_only_contract() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type, no_type_check
        from snektest import test_typing

        @test_typing
        @no_type_check
        def consumer(symbolic: int) -> None:
            assert_type(symbolic, str)
        """),
        name="test_opaque_static_decorator",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 2, msg=result["stdout"] + result["stderr"])
    assert_in("single bare @test_typing", result["error"]["message"])


@test(mark="slow")
def test_same_file_contracts_share_checker_work_but_later_runs_check_fresh() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import test_typing

        @test_typing
        def first(symbolic: int) -> None:
            assert_type(symbolic, int)

        @test_typing
        def second(symbolic: str) -> None:
            assert_type(symbolic, str)
        """),
        name="test_batched_contracts",
    )
    driver = directory / "batch_driver.py"
    driver.write_text(
        dedent("""
        import asyncio
        from pathlib import Path
        import sys
        from snektest import assert_eq
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem

        starts: list[str] = []

        def observe(event: str, arguments: tuple[object, ...]) -> None:
            if event == "subprocess.Popen" and Path(str(arguments[0])).name in ("ty", "ty.exe"):
                starts.append(str(arguments[0]))

        async def main() -> None:
            sys.addaudithook(observe)
            for _ in range(2):
                starts.clear()
                result = await run_tests_programmatic([FilterItem(sys.argv[1])])
                assert_eq(result.passed, 2)
                assert_eq(len(starts), 1)
            print("batches are run-scoped")

        asyncio.run(main())
        """),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(driver), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("batches are run-scoped", completed.stdout)


@test([Param(None, name="local"), Param(1, name="worker")], mark="slow")
def test_explicit_ty_configuration_preserves_required_assertion_checks(
    workers: int | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    config = directory / "contracts.toml"
    config.write_text('[rules]\nall = "ignore"\n', encoding="utf-8")
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import assert_type_error, test_typing

        class Parent:
            def value(self) -> int:
                return 1

        class Child(Parent):
            def value(self) -> int:
                return 1

        @test_typing
        def healthy(symbolic: int) -> None:
            assert_type(symbolic, int)

        @test_typing
        def broken(symbolic: int) -> None:
            assert_type(symbolic, str)

        @test_typing
        def missing() -> None:
            with assert_type_error("invalid-argument-type"):
                pass
        """),
        name="test_configured_contracts",
    )
    arguments = [] if workers is None else ["--workers", str(workers)]
    result = run_test_subprocess(
        path, "--typing-config", str(config), *arguments, timeout=30
    )
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq((result["errors"], result["failed"], result["passed"]), (0, 2, 1))
    assert_in("type-assertion-failure", result["tests"][1]["exception"]["message"])
    assert_in("no diagnostics", result["tests"][2]["exception"]["message"])


@test(mark="slow")
async def test_programmatic_configuration_is_explicit_and_does_not_leak_between_runs() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture())
    config = directory / "relaxed.toml"
    config.write_text(
        '[rules]\nmissing-override-decorator = "ignore"\n', encoding="utf-8"
    )
    path = create_test_file(
        directory,
        dedent("""
        from typing import assert_type
        from snektest import test_typing

        class Parent:
            def value(self) -> int:
                return 1

        class Child(Parent):
            def value(self) -> int:
                return 1

        @test_typing
        def consumer(symbolic: int) -> None:
            assert_type(symbolic, int)
        """),
        name="test_programmatic_configuration",
    )
    configured = await run_tests_programmatic(
        [FilterItem(str(path))], typing_config=config
    )
    assert_eq((configured.errors, configured.failed, configured.passed), (0, 0, 1))
    default = await run_tests_programmatic([FilterItem(str(path))])
    assert_eq((default.errors, default.failed, default.passed), (0, 1, 0))


@test(mark="slow")
def test_invalid_native_configuration_is_an_error_and_cleans_up() -> None:
    directory = load_fixture(tmp_dir_fixture())
    config = directory / "malformed.toml"
    config.write_text("[rules", encoding="utf-8")
    path = create_test_file(
        directory,
        dedent("""
        from snektest import test, test_typing

        @test_typing
        def consumer() -> None:
            pass

        @test(mark="fast")
        def following() -> None:
            pass
        """),
        name="test_bad_native_configuration",
    )
    scratch = directory / "bad-config-scratch"
    scratch.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--typing-config",
            str(config),
            str(path),
        ],
        env=dict(os.environ, TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    result = json.loads(completed.stdout)
    assert_eq((result["errors"], result["failed"], result["passed"]), (1, 0, 1))
    assert_in(
        "ty invocation failed with status 2", result["tests"][0]["exception"]["message"]
    )
    assert_eq(list(scratch.iterdir()), [])


@test(mark="slow")
def test_reexported_marker_is_rejected_rather_than_treated_as_an_ordinary_context() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture()) / "reexport_package"
    directory.mkdir()
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "marker_exports.py").write_text(
        "from snektest import assert_type_error as reject\n", encoding="utf-8"
    )
    path = create_test_file(
        directory,
        dedent("""
        from .marker_exports import reject
        from snektest import test_typing

        @test_typing
        def consumer() -> None:
            with reject("invalid-argument-type"):
                pass
        """),
        name="test_reexported_static_marker",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 2, msg=result["stdout"] + result["stderr"])
    assert_in("copied or indirect marker values", result["error"]["message"])


@test(mark="slow")
def test_config_edits_invalidate_same_run_report_reuse() -> None:
    directory = load_fixture(tmp_dir_fixture())
    config = directory / "changing-rules.toml"
    config.write_text('[rules]\nall = "ignore"\n', encoding="utf-8")
    path = create_test_file(
        directory,
        dedent("""
        from pathlib import Path
        from typing import assert_type
        from snektest import test, test_typing

        class Parent:
            def value(self) -> int:
                return 1

        class Child(Parent):
            def value(self) -> int:
                return 1

        @test_typing
        def first(symbolic: int) -> None:
            assert_type(symbolic, int)

        @test(mark="medium")
        def update_rules() -> None:
            Path(__file__).with_name("changing-rules.toml").write_text(
                '[rules]\\nall = "error"\\n', encoding="utf-8",
            )

        @test_typing
        def second(symbolic: int) -> None:
            assert_type(symbolic, int)
        """),
        name="test_changing_configuration",
    )
    result = run_test_subprocess(path, "--typing-config", str(config), timeout=30)
    assert_eq(result["returncode"], 1, msg=result["stdout"] + result["stderr"])
    assert_eq((result["errors"], result["failed"], result["passed"]), (0, 1, 2))
    assert_in("missing-override-decorator", result["tests"][2]["exception"]["message"])


@test(
    [
        Param(
            ("import snektest.type_contracts as contracts", "contracts"), name="aliased"
        ),
        Param(
            ("import snektest.type_contracts", "snektest.type_contracts"), name="dotted"
        ),
        Param(
            ("from snektest import type_contracts as contracts", "contracts"),
            name="from-aliased",
        ),
        Param(
            ("from snektest import type_contracts", "type_contracts"),
            name="from-module",
        ),
    ],
    mark="slow",
)
def test_qualified_implementation_module_imports_declare_contracts(
    declaration: tuple[str, str],
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    statement, reference = declaration
    path = create_test_file(
        directory,
        dedent(f"""
        {statement}
        from typing import assert_type
        def takes_int(value: int) -> int:
            return value
        @{reference}.test_typing
        def consumer(symbolic: int) -> None:
            assert_type(takes_int(symbolic), int)
            with {reference}.assert_type_error("invalid-argument-type"):
                takes_int("wrong")
            import snektest.type_contracts as local_contracts
            with local_contracts.assert_type_error("invalid-argument-type"):
                takes_int("also wrong")
        """),
        name=f"test_qualified_{reference.replace('.', '_')}",
    )
    result = run_test_subprocess(path, timeout=30)
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 1)
    normal = subprocess.run(
        [sys.executable, "-m", "ty", "check", "--error", "all", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(normal.returncode, 0, msg=normal.stdout + normal.stderr)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "assert_type(takes_int(symbolic), int)",
            "assert_type(takes_int(symbolic), str)",
        ),
        encoding="utf-8",
    )
    broken = run_test_subprocess(path, timeout=30)
    assert_eq((broken["errors"], broken["failed"], broken["passed"]), (0, 1, 0))
