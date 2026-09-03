"""Subprocess regressions for conservative memory measurements."""

from textwrap import dedent

from snektest import assert_eq, assert_lt, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess

_KB = 1024
_MB = 1024 * 1024


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
