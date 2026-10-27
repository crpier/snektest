"""The public copy-and-run examples teach usable parameter and assertion APIs."""

import subprocess
import sys
from pathlib import Path

from snektest import assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


def _copy_example(directory: Path, example: str) -> Path:
    printed = subprocess.run(
        [sys.executable, "-m", "snektest", "--example", example],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert_eq(printed.returncode, 0, msg=printed.stdout + printed.stderr)
    return create_test_file(directory, printed.stdout, name=f"test_{example}")


@test(mark="slow")
def test_printed_tuple_example_has_a_runnable_named_case() -> None:
    directory = load_fixture(tmp_dir_fixture())
    example = _copy_example(directory, "parametrize")
    selector = f"{example}::test_tuple_value[small]"
    collection = run_test_subprocess(example, "--collect-only")
    assert_eq(collection["returncode"], 0, msg=collection["stdout"])
    assert_in(selector, [case["name"] for case in collection["tests"]])

    executed = run_test_subprocess(Path(selector))
    assert_eq(executed["returncode"], 0, msg=executed["stdout"])
    assert_eq(executed["passed"], 1)


@test(mark="slow")
def test_printed_parameter_example_runs_scalar_tuple_axes_and_literal_cases() -> None:
    directory = load_fixture(tmp_dir_fixture())
    example = _copy_example(directory, "parametrize")
    collection = run_test_subprocess(example, "--collect-only")
    assert_eq(collection["returncode"], 0, msg=collection["stdout"])
    assert_eq(
        [case["name"].removeprefix(f"{example}::") for case in collection["tests"]],
        [
            "test_string_length[lowercase]",
            "test_string_length[uppercase]",
            "test_string_length[mixed]",
            "test_tuple_value[small]",
            "test_tuple_value[large]",
            "test_cartesian_product[hello, world]",
            "test_cartesian_product[hello, there]",
            "test_cartesian_product[hi, world]",
            "test_cartesian_product[hi, there]",
            "test_literal_backend[sqlite]",
            "test_literal_backend[mariadb]",
        ],
    )
    executed = run_test_subprocess(example)
    assert_eq(executed["returncode"], 0, msg=executed["stdout"])
    assert_eq(executed["passed"], 11)


@test(mark="slow")
def test_printed_basic_example_checks_original_exception_causes() -> None:
    directory = load_fixture(tmp_dir_fixture())
    example = _copy_example(directory, "basic")
    executed = run_test_subprocess(Path(f"{example}::test_exception_cause"))
    assert_eq(executed["returncode"], 0, msg=executed["stdout"])
    assert_eq(executed["passed"], 1)


@test(mark="slow")
def test_printed_basic_example_binds_narrowed_values() -> None:
    directory = load_fixture(tmp_dir_fixture())
    example = _copy_example(directory, "basic")
    executed = run_test_subprocess(Path(f"{example}::test_bound_narrowing_helpers"))
    assert_eq(executed["returncode"], 0, msg=executed["stdout"])
    assert_eq(executed["passed"], 1)
