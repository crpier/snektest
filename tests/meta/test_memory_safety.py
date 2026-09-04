"""Subprocess regressions for conservative memory measurements."""

import os
import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, assert_lt, assert_memory, load_fixture, test
from snektest.collection import collect_tests_from_filters
from snektest.models import FilterItem, TestCase
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess

_KB = 1024
_MB = 1024 * 1024


@test(
    [
        Param(value=(10, 100, 4 * _MB), name="1000-cases"),
        Param(value=(100, 100, 24 * _MB), name="10000-cases"),
    ],
    mark="medium",
)
def test_parameter_matrix_collection_stays_within_allocation_budget(
    scenario: tuple[int, int, int],
) -> None:
    """Collected plans have measured memory budgets at supported cardinalities."""
    first_axis_size, second_axis_size, peak_budget = scenario
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from snektest import Param, test

            FIRST = [Param(value=index, name=str(index)) for index in range({first_axis_size})]
            SECOND = [Param(value=index, name=str(index)) for index in range({second_axis_size})]

            @test(FIRST, SECOND)
            def test_case(first: int, second: int) -> None:
                _ = first + second
        """),
        name=f"test_{first_axis_size}_by_{second_axis_size}_matrix",
    )

    test_cases: list[TestCase] = []
    with assert_memory(peak_below=peak_budget):
        test_cases = collect_tests_from_filters([FilterItem(str(test_file))])

    assert_eq(len(test_cases), first_axis_size * second_axis_size)


@test(mark="slow")
def test_noisy_passing_console_run_stays_within_allocation_budget() -> None:
    """Discarded pass output does not accumulate across a large run."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import Param, test

            CASES = [Param(value=index, name=str(index)) for index in range(1000)]
            NOISE = "x" * 65536

            @test(CASES)
            def test_noisy(index: int) -> None:
                print(NOISE, index)
        """),
        name="test_noisy_passes",
    )
    environment = {**os.environ, "SNEKTEST_MEMORY_SUITE": str(test_file)}
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import os
import tracemalloc

from snektest.cli import run_tests_programmatic
from snektest.models import FilterItem
from snektest.reporting import NullRunReporter

async def main() -> None:
    tracemalloc.start()
    summary = await run_tests_programmatic(
        [FilterItem(os.environ["SNEKTEST_MEMORY_SUITE"])],
        reporter=NullRunReporter(retain_passed_output=False),
    )
    _, peak_bytes = tracemalloc.get_traced_memory()
    print(summary.total_tests, peak_bytes)

asyncio.run(main())
""",
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    total_tests, peak_bytes = (int(value) for value in process.stdout.split())

    assert_eq(total_tests, 1000)
    assert_lt(peak_bytes, 8 * _MB)


@test(mark="slow")
def test_large_failing_run_stays_within_allocation_budget() -> None:
    """Failure snapshots release large frame locals while retaining diagnostics."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import Param, test

            CASES = [Param(value=index, name=str(index)) for index in range(1000)]

            @test(CASES)
            def test_failure(index: int) -> None:
                payload = bytearray(65536)
                if payload:
                    raise RuntimeError(index)
        """),
        name="test_large_failing_suite",
    )
    environment = {**os.environ, "SNEKTEST_MEMORY_SUITE": str(test_file)}
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import os
import tracemalloc

from snektest.cli import run_tests_programmatic
from snektest.models import FilterItem

async def main() -> None:
    tracemalloc.start()
    summary = await run_tests_programmatic(
        [FilterItem(os.environ["SNEKTEST_MEMORY_SUITE"])],
    )
    _, peak_bytes = tracemalloc.get_traced_memory()
    print(summary.errors, peak_bytes)

asyncio.run(main())
""",
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    errors, peak_bytes = (int(value) for value in process.stdout.split())

    assert_eq(errors, 1000)
    assert_lt(peak_bytes, 16 * _MB)


@test(mark="slow")
def test_import_allocations_do_not_contaminate_memory_measurement() -> None:
    """Collection finishes before the backend establishes its test baseline."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from snektest import assert_memory, test

            IMPORT_ALLOCATION = bytearray({2 * _MB})

            @test()
            def test_small_region() -> None:
                with assert_memory(
                    peak_below={64 * _KB},
                    rounds=3,
                    warmup=2,
                ) as measurement:
                    for _ in measurement.rounds:
                        pass
        """),
        name="test_import_allocation",
    )

    result = run_test_subprocess(test_file)
    measurement = result["tests"][0]["memory_measurements"][0]

    assert_eq(result["returncode"], 0)
    assert_eq(result["passed"], 1)
    assert_eq(measurement["rounds"], 3)
    assert_eq(measurement["peak_budget"], 64 * _KB)
    assert_eq(measurement["slope_budget"], None)
    assert_lt(measurement["peak_bytes"], 64 * _KB)
