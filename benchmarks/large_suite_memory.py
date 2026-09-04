"""Measure collection and execution memory for large-suite scenarios."""

from __future__ import annotations

import asyncio
import json
import tempfile
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal

from snektest.cli import run_tests_programmatic
from snektest.collection import collect_tests_from_filters
from snektest.models import FilterItem
from snektest.reporting import NullRunReporter


@dataclass(frozen=True)
class Measurement:
    """One benchmark scenario's retained and peak traced allocations."""

    cases: int
    elapsed_seconds: float
    peak_bytes: int
    retained_bytes: int
    scenario: str


def _write_suite(directory: Path, name: str, source: str) -> Path:
    suite_path = directory / f"test_{name}.py"
    _ = suite_path.write_text(source)
    return suite_path


def _measure_collection(first_axis: int, second_axis: int) -> Measurement:
    with tempfile.TemporaryDirectory() as temporary_directory:
        suite_path = _write_suite(
            Path(temporary_directory),
            f"matrix_{first_axis}_{second_axis}",
            f"""from snektest import Param, test

FIRST = [Param(value=index, name=str(index)) for index in range({first_axis})]
SECOND = [Param(value=index, name=str(index)) for index in range({second_axis})]

@test(FIRST, SECOND)
def test_case(first: int, second: int) -> None:
    _ = first + second
""",
        )
        tracemalloc.start()
        started_at = perf_counter()
        test_cases = collect_tests_from_filters([FilterItem(str(suite_path))])
        elapsed_seconds = perf_counter() - started_at
        retained_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    return Measurement(
        cases=len(test_cases),
        elapsed_seconds=elapsed_seconds,
        peak_bytes=peak_bytes,
        retained_bytes=retained_bytes,
        scenario=f"collect-{first_axis}x{second_axis}",
    )


async def _measure_run(
    scenario: str,
    source: str,
    *,
    workers: int | Literal["auto"] | None = None,
) -> Measurement:
    with tempfile.TemporaryDirectory() as temporary_directory:
        suite_path = _write_suite(Path(temporary_directory), scenario, source)
        tracemalloc.start()
        started_at = perf_counter()
        summary = await run_tests_programmatic(
            [FilterItem(str(suite_path))],
            reporter=NullRunReporter(retain_passed_output=False),
            workers=workers,
        )
        elapsed_seconds = perf_counter() - started_at
        retained_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    return Measurement(
        cases=summary.total_tests,
        elapsed_seconds=elapsed_seconds,
        peak_bytes=peak_bytes,
        retained_bytes=retained_bytes,
        scenario=scenario,
    )


async def main() -> None:
    """Run every large-suite benchmark and print machine-readable measurements."""
    measurements = [
        _measure_collection(10, 100),
        _measure_collection(100, 100),
        await _measure_run(
            "noisy-passes",
            """from snektest import Param, test

CASES = [Param(value=index, name=str(index)) for index in range(1000)]
NOISE = "x" * 65536

@test(CASES)
def test_noisy(index: int) -> None:
    print(NOISE, index)
""",
        ),
        await _measure_run(
            "large-failing-suite",
            """from snektest import Param, test

CASES = [Param(value=index, name=str(index)) for index in range(1000)]

@test(CASES)
def test_failure(index: int) -> None:
    payload = bytearray(65536)
    if payload:
        raise RuntimeError(index)
""",
        ),
        await _measure_run(
            "slow-first-worker-run",
            """import asyncio

from snektest import Param, test

CASES = [Param(value=index, name=str(index)) for index in range(999)]

@test()
async def test_slow_first() -> None:
    await asyncio.sleep(0.25)

@test(CASES)
def test_fast(index: int) -> None:
    _ = index
""",
            workers=2,
        ),
    ]
    print(json.dumps([asdict(measurement) for measurement in measurements], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
