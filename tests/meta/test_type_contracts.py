"""Python-native static contracts through real runner and checker interfaces."""

import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
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
