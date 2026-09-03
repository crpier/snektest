"""Tests for timing-budget assertions and benchmark reporting."""

import asyncio
import gc
import json
from collections.abc import Generator
from pathlib import Path

from rich.console import Console

from snektest import (
    assert_benchmark,
    assert_eq,
    assert_false,
    assert_isinstance,
    assert_raises,
    assert_true,
    fixture,
    load_fixture,
    test,
)
from snektest.benchmark import BenchmarkContext, collect_benchmarks
from snektest.cli import TestRunSummary, build_json_summary
from snektest.execution import execute_test
from snektest.models import (
    AssertionFailure,
    BadRequestError,
    BenchmarkMeasurement,
    FailedResult,
    PassedResult,
    TestCase,
    TestName,
    TestResult,
)
from snektest.presenter import print_test_result_to_console


class _Clock:
    """Deterministic nanosecond clock for exact timing statistics."""

    def __init__(self, readings: list[int]) -> None:
        self._readings: list[int] = readings
        self._index: int = 0

    def __call__(self) -> int:
        reading = self._readings[self._index]
        self._index += 1
        return reading


@test(mark="fast")
def test_benchmark_calculates_statistics_after_warmup() -> None:
    """Warmup is discarded and measured durations produce exact statistics."""
    invocations = 0
    timing = BenchmarkContext(
        median_below=1,
        rounds=5,
        warmup=1,
        disable_gc=False,
        clock=_Clock(
            [
                0,
                50,
                100,
                1_100,
                2_000,
                4_000,
                5_000,
                9_000,
                10_000,
                18_000,
                20_000,
                36_000,
            ]
        ),
    )

    with timing:
        for _ in timing.rounds:
            invocations += 1

    assert_eq(invocations, 6)
    assert_eq(timing.min_seconds, 0.000001)
    assert_eq(timing.median_seconds, 0.000004)
    assert_eq(timing.p95_seconds, 0.000016)
    assert_eq(timing.mean_seconds, 0.0000062)
    assert_eq(round(timing.stddev_seconds, 10), 0.0000054553)


@test(mark="fast")
def test_benchmark_excludes_fixture_setup_before_context() -> None:
    """A fixture loaded before timing is set up once rather than once per round."""
    setup_calls: list[None] = []

    @fixture
    def resource() -> Generator[int]:
        setup_calls.append(None)
        yield 1

    resource_value = load_fixture(resource())

    with assert_benchmark(median_below=1, rounds=3, warmup=1) as timing:
        for _ in timing.rounds:
            _ = resource_value + 1

    assert_eq(len(setup_calls), 1)


@test(mark="fast")
def test_benchmark_fails_when_median_reaches_budget() -> None:
    """The median budget uses a strict less-than comparison."""
    timing = BenchmarkContext(
        median_below=0.000001,
        rounds=2,
        warmup=0,
        disable_gc=False,
        clock=_Clock([0, 1_000, 2_000, 3_000]),
    )

    with assert_raises(AssertionFailure) as failure, timing:
        for _ in timing.rounds:
            pass

    assert_eq(failure.exception.actual, 0.000001)
    assert_eq(failure.exception.expected, 0.000001)
    assert_eq(failure.exception.operator, "<")


@test(mark="fast")
def test_named_benchmark_failure_identifies_region() -> None:
    """A budget failure identifies the timed region that exceeded it."""
    timing = BenchmarkContext(
        median_below=0.000001,
        name="query",
        rounds=1,
        warmup=0,
        disable_gc=False,
        clock=_Clock([0, 1_000]),
    )

    with assert_raises(AssertionFailure) as failure, timing:
        for _ in timing.rounds:
            pass

    assert_eq(
        str(failure.exception),
        "benchmark[query] median 1e-06s is not below budget 1e-06s",
    )


@test(mark="fast")
def test_benchmark_fails_when_p95_reaches_budget() -> None:
    """A p95-only budget catches tail latency even when the median is lower."""
    timing = BenchmarkContext(
        median_below=None,
        p95_below=0.00001,
        rounds=5,
        warmup=0,
        disable_gc=False,
        clock=_Clock(
            [0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000, 8_000, 18_000]
        ),
    )

    with assert_raises(AssertionFailure) as failure, timing:
        for _ in timing.rounds:
            pass

    assert_eq(failure.exception.actual, 0.00001)
    assert_eq(failure.exception.expected, 0.00001)
    assert_eq(failure.exception.operator, "<")


@test(mark="fast")
def test_benchmark_rejects_non_positive_rounds() -> None:
    """At least one measured round is required."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(median_below=1, rounds=0) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_benchmark_rejects_negative_warmup() -> None:
    """Warmup may be zero but cannot be negative."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(median_below=1, warmup=-1) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_benchmark_rejects_invalid_budget() -> None:
    """Median budgets must be finite and positive seconds."""
    for budget in (0, -1, float("inf"), float("nan")):
        with (
            assert_raises(BadRequestError),
            assert_benchmark(median_below=budget) as timing,
        ):
            for _ in timing.rounds:
                pass

    with (
        assert_raises(BadRequestError),
        assert_benchmark(p95_below=float("inf")) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_benchmark_rejects_missing_budget() -> None:
    """Unchecked callers still must supply at least one timing budget."""
    with (
        assert_raises(BadRequestError),
        BenchmarkContext(
            median_below=None,
            p95_below=None,
            rounds=1,
            warmup=0,
            disable_gc=False,
        ) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_benchmark_rejects_blank_name() -> None:
    """A named timing region needs a stable, nonblank identifier."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(name="  ", median_below=1) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_regression_gate_requires_stable_name() -> None:
    """A persisted comparison cannot rely on an unnamed region's call order."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(
            median_below=1,
            median_regression_below=0.1,
            rounds=1,
            warmup=0,
        ) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
def test_benchmark_rejects_invalid_regression_policy() -> None:
    """Relative limits and absolute floors must be finite and meaningful."""
    for regression_limit in (0, -1, float("inf"), float("nan")):
        with (
            assert_raises(BadRequestError),
            assert_benchmark(
                name="query",
                median_below=1,
                median_regression_below=regression_limit,
                rounds=1,
                warmup=0,
            ) as timing,
        ):
            for _ in timing.rounds:
                pass

    with (
        assert_raises(BadRequestError),
        assert_benchmark(
            name="query",
            median_below=1,
            regression_noise_floor=0.1,
            rounds=1,
            warmup=0,
        ) as timing,
    ):
        for _ in timing.rounds:
            pass


@test(mark="fast")
async def test_absolute_failure_retains_completed_measurement() -> None:
    """A complete timing remains reportable when its local budget fails."""

    def benchmark_body() -> None:
        timing = BenchmarkContext(
            median_below=0.000001,
            rounds=1,
            warmup=0,
            disable_gc=False,
            clock=_Clock([0, 1_000]),
        )
        with timing:
            for _ in timing.rounds:
                pass

    test_result = await execute_test(
        TestCase(
            function=benchmark_body,
            markers=("fast",),
            name=TestName(
                file_path=Path("test_benchmark.py"),
                func_name="benchmark_body",
                params_part="",
            ),
        )
    )

    failure = assert_isinstance(test_result.result, FailedResult)
    assert_eq(len(failure.benchmarks), 1)
    assert_eq(failure.benchmarks[0].median_seconds, 0.000001)


@test(mark="fast")
def test_benchmark_requires_rounds_iterator() -> None:
    """A benchmark block that never loops cannot produce a measurement."""
    with assert_raises(BadRequestError), assert_benchmark(median_below=1):
        pass


@test(mark="fast")
def test_benchmark_rejects_partial_rounds_iterator() -> None:
    """Breaking early is rejected instead of reporting incomplete statistics."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(median_below=1, rounds=2, warmup=0) as timing,
    ):
        for _ in timing.rounds:
            break


@test(mark="fast")
def test_benchmark_statistics_are_unavailable_before_exit() -> None:
    """Statistics cannot be observed before all rounds finish."""
    with (
        assert_raises(BadRequestError),
        assert_benchmark(median_below=1, rounds=1, warmup=0) as timing,
    ):
        for _ in timing.rounds:
            _ = timing.median_seconds


@test(mark="fast")
def test_benchmark_body_error_does_not_record_measurement() -> None:
    """A body error propagates and never leaves a passing benchmark result."""
    with (
        collect_benchmarks() as measurements,
        assert_raises(RuntimeError),
        assert_benchmark(median_below=1, rounds=1, warmup=0) as timing,
    ):
        for _ in timing.rounds:
            msg = "body failed"
            raise RuntimeError(msg)

    assert_eq(measurements.measurements, [])


@test(mark="fast")
def test_benchmark_disables_gc_only_for_measured_rounds() -> None:
    """Warmup sees normal GC while measured rounds see it suspended."""
    gc_was_enabled = gc.isenabled()
    gc.enable()
    observed_gc_states: list[bool] = []

    try:
        with assert_benchmark(median_below=1, rounds=2, warmup=1) as timing:
            observed_gc_states.extend(gc.isenabled() for _ in timing.rounds)
    finally:
        if not gc_was_enabled:
            gc.disable()

    assert_eq(observed_gc_states, [True, False, False])
    assert_eq(gc.isenabled(), gc_was_enabled)


@test(mark="fast")
def test_benchmark_can_leave_gc_enabled() -> None:
    """The opt-out does not alter GC state during measured rounds."""
    gc_was_enabled = gc.isenabled()
    gc.enable()

    try:
        with assert_benchmark(
            median_below=1, rounds=1, warmup=0, disable_gc=False
        ) as timing:
            for _ in timing.rounds:
                assert_true(gc.isenabled())
    finally:
        if not gc_was_enabled:
            gc.disable()


@test(mark="fast")
def test_benchmark_restores_previously_disabled_gc() -> None:
    """Benchmark cleanup preserves a caller's pre-existing disabled GC state."""
    gc_was_enabled = gc.isenabled()
    gc.disable()

    try:
        with assert_benchmark(median_below=1, rounds=1, warmup=0) as timing:
            for _ in timing.rounds:
                assert_false(gc.isenabled())
        assert_false(gc.isenabled())
    finally:
        if gc_was_enabled:
            gc.enable()


@test(mark="fast")
async def test_async_operation_is_timed_on_current_event_loop() -> None:
    """The rounds iterator directly surrounds awaited work."""
    with assert_benchmark(median_below=1, rounds=2, warmup=1) as timing:
        for _ in timing.rounds:
            await asyncio.sleep(0)

    assert_true(timing.median_seconds > 0)


@test(mark="fast")
async def test_async_overlap_does_not_corrupt_gc_state() -> None:
    """A concurrent context is rejected without changing the first context's GC."""
    gc_was_enabled = gc.isenabled()
    gc.enable()
    first_round_started = asyncio.Event()
    release_first_round = asyncio.Event()

    async def run_first_benchmark() -> None:
        with assert_benchmark(median_below=1, rounds=1, warmup=0) as timing:
            for _ in timing.rounds:
                first_round_started.set()
                _ = await release_first_round.wait()

    first_benchmark = asyncio.create_task(run_first_benchmark())
    try:
        _ = await first_round_started.wait()
        with (
            assert_raises(BadRequestError) as failure,
            assert_benchmark(median_below=1, rounds=1, warmup=0) as timing,
        ):
            for _ in timing.rounds:
                pass

        assert_eq(
            str(failure.exception),
            "assert_benchmark contexts cannot overlap because concurrent regions distort timings and GC state.",
        )
        assert_false(gc.isenabled())
        release_first_round.set()
        await first_benchmark
        assert_true(gc.isenabled())
    finally:
        release_first_round.set()
        try:
            await first_benchmark
        finally:
            if not gc_was_enabled:
                gc.disable()


@test(mark="fast")
async def test_execution_collects_passing_benchmark() -> None:
    """A successful benchmark is attached to its structured test result."""

    async def benchmark_body() -> None:
        with assert_benchmark(median_below=1, rounds=2, warmup=0) as timing:
            for _ in timing.rounds:
                await asyncio.sleep(0)

    test_result = await execute_test(
        TestCase(
            function=benchmark_body,
            markers=("fast",),
            name=TestName(
                file_path=Path("test_benchmark.py"),
                func_name="benchmark_body",
                params_part="",
            ),
        )
    )

    passed = assert_isinstance(test_result.result, PassedResult)
    assert_eq(len(passed.benchmarks), 1)
    assert_eq(passed.benchmarks[0].rounds, 2)


@test(mark="fast")
async def test_execution_distinguishes_named_benchmark_regions() -> None:
    """Multiple regions in one test retain their names and collection order."""

    async def benchmark_body() -> None:
        for region_name in ("encode", "write"):
            with assert_benchmark(
                name=region_name, median_below=1, rounds=1, warmup=0
            ) as timing:
                for _ in timing.rounds:
                    await asyncio.sleep(0)

    test_result = await execute_test(
        TestCase(
            function=benchmark_body,
            markers=("fast",),
            name=TestName(
                file_path=Path("test_benchmark.py"),
                func_name="benchmark_body",
                params_part="",
            ),
        )
    )

    passed = assert_isinstance(test_result.result, PassedResult)
    assert_eq([benchmark.name for benchmark in passed.benchmarks], ["encode", "write"])


def _benchmark_test_result() -> TestResult:
    return TestResult(
        name=TestName(
            file_path=Path("tests/test_fake.py"),
            func_name="test_benchmark_example",
            params_part="",
        ),
        duration=0.1,
        result=PassedResult(
            benchmarks=(
                BenchmarkMeasurement(
                    name="query",
                    rounds=5,
                    warmup=1,
                    min_seconds=0.000001,
                    median_seconds=0.000002,
                    p95_seconds=0.000004,
                    mean_seconds=0.0000025,
                    stddev_seconds=0.000001,
                    median_budget_seconds=0.00001,
                    p95_budget_seconds=0.00002,
                ),
            )
        ),
        markers=("fast",),
        captured_output="",
        fixture_teardown_failures=(),
        fixture_teardown_output=None,
        warnings=(),
    )


@test(mark="fast")
def test_benchmark_statistics_render_on_result_line() -> None:
    """Human output distinguishes statistics, budget, and test duration."""
    console = Console(record=True)

    print_test_result_to_console(console, _benchmark_test_result())

    output = console.export_text()
    assert_true("benchmark[query] min=" in output)
    assert_true("median=" in output)
    assert_true("(<10.0us)" in output)
    assert_true("p95=" in output)
    assert_true("p95=4.0us (<20.0us)" in output)
    assert_true("mean=" in output)
    assert_true("stddev=" in output)
    assert_true("(5 rounds, 1 warmup)" in output)


@test(mark="fast")
def test_benchmark_statistics_are_in_json_output() -> None:
    """Machine-readable results expose timings and the median budget."""
    summary = TestRunSummary(
        total_tests=1,
        passed=1,
        skipped=0,
        expected_failures=0,
        unexpected_passes=0,
        failed=0,
        errors=0,
        fixture_teardown_failed=0,
        session_teardown_failed=0,
        test_results=[_benchmark_test_result()],
        session_teardown_failures=[],
    )

    output = json.loads(json.dumps(build_json_summary(summary)))

    benchmark = output["tests"][0]["benchmark_measurements"][0]
    assert_eq(benchmark["name"], "query")
    assert_eq(benchmark["rounds"], 5)
    assert_eq(benchmark["warmup"], 1)
    assert_eq(benchmark["median_seconds"], 0.000002)
    assert_eq(benchmark["median_budget_seconds"], 0.00001)
    assert_eq(benchmark["p95_budget_seconds"], 0.00002)
