"""Integration tests for spawned process execution and run fixture ownership."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from snektest import assert_eq, assert_in, test
from testutils.helpers import run_test_subprocess


@test(mark="slow")
def test_workers_run_in_children_and_report_manifest_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_worker_order.py"
        _ = test_file.write_text(
            """
import asyncio
import os

from snektest import assert_ne, test

@test()
async def test_slow() -> None:
    assert_ne(os.getpid(), os.getppid())
    await asyncio.sleep(0.05)

@test()
def test_fast() -> None:
    assert_ne(os.getpid(), os.getppid())
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "2", timeout=10)

    assert_eq(result["returncode"], 0)
    assert_eq(result["passed"], 2)
    assert_eq(
        [entry["name"].rsplit("::", 1)[-1] for entry in result["tests"]],
        ["test_slow", "test_fast"],
    )


@test(mark="slow")
def test_run_fixture_is_owned_once_by_host_and_copied_to_workers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        lifecycle_file = directory / "lifecycle.jsonl"
        worker_file = directory / "workers.jsonl"
        test_file = directory / "test_run_fixture.py"
        _ = test_file.write_text(
            f"""
import json
import os
from collections.abc import Generator
from pathlib import Path

from snektest import assert_ne, fixture, load_fixture, test

LIFECYCLE = Path({str(lifecycle_file)!r})
WORKERS = Path({str(worker_file)!r})

@fixture(scope="run")
def shared_descriptor() -> Generator[dict[str, int]]:
    descriptor = {{"host_pid": os.getpid()}}
    with LIFECYCLE.open("a") as output:
        _ = output.write(json.dumps({{"event": "setup", **descriptor}}) + "\\n")
    yield descriptor
    with LIFECYCLE.open("a") as output:
        _ = output.write(json.dumps({{"event": "teardown", **descriptor}}) + "\\n")

def record_worker() -> None:
    descriptor = load_fixture(shared_descriptor())
    assert_ne(os.getpid(), descriptor["host_pid"])
    with WORKERS.open("a") as output:
        _ = output.write(json.dumps({{"worker_pid": os.getpid(), **descriptor}}) + "\\n")

@test()
def test_one() -> None:
    record_worker()

@test()
def test_two() -> None:
    record_worker()
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "2", timeout=10)
        lifecycle = [
            json.loads(line) for line in lifecycle_file.read_text().splitlines()
        ]
        workers = [json.loads(line) for line in worker_file.read_text().splitlines()]

    assert_eq(result["returncode"], 0)
    assert_eq([event["event"] for event in lifecycle], ["setup", "teardown"])
    assert_eq(len({entry["host_pid"] for entry in workers}), 1)
    assert_eq(len({entry["worker_pid"] for entry in workers}), 2)


@test(mark="slow")
def test_run_fixture_publication_failure_does_not_stop_unrelated_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_run_fixture_publication.py"
        _ = test_file.write_text(
            """
from collections.abc import Generator

from snektest import fixture, load_fixture, test

@fixture(scope="run")
def bad_descriptor() -> Generator[object]:
    yield lambda: None

@test()
def test_bad_descriptor() -> None:
    _ = load_fixture(bad_descriptor())

@test()
def test_unrelated() -> None:
    pass
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "2", timeout=10)

    assert_eq(result["returncode"], 1)
    assert_eq(result["errors"], 1)
    assert_eq(result["passed"], 1)
    assert_in("publication failed", result["tests"][0]["exception"]["message"])


@test(mark="slow")
def test_run_fixture_teardown_failure_has_distinct_summary_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_run_fixture_teardown.py"
        _ = test_file.write_text(
            """
from collections.abc import Generator

from snektest import fixture, load_fixture, test

@fixture(scope="run")
def descriptor() -> Generator[str]:
    yield "ready"
    raise RuntimeError("run teardown failed")

@test()
def test_uses_descriptor() -> None:
    _ = load_fixture(descriptor())
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "1", timeout=10)

    assert_eq(result["returncode"], 1)
    assert_eq(result["passed"], 1)
    assert_eq(result["run_teardown_failed"], 1)
    assert_eq(result["session_teardown_failed"], 0)
    assert_in(
        "run teardown failed",
        result["run_teardown_failures"][0]["exception"]["message"],
    )


@test(mark="slow")
def test_run_fixture_output_does_not_corrupt_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_run_fixture_output.py"
        _ = test_file.write_text(
            """
from collections.abc import Generator

from snektest import fixture, load_fixture, test

@fixture(scope="run")
def descriptor() -> Generator[str]:
    print("run setup output")
    yield "ready"
    print("run teardown output")

@test()
def test_uses_descriptor() -> None:
    _ = load_fixture(descriptor())
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "1", timeout=10)

    assert_eq(result["returncode"], 0)
    assert_eq(result["passed"], 1)
    assert_eq(result["stdout"].count("\n"), 1)


@test(mark="slow")
def test_distinct_first_run_fixture_loads_publish_before_tests_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        second_loaded = directory / "second-loaded"
        test_file = directory / "test_distinct_run_fixtures.py"
        _ = test_file.write_text(
            f"""
import time
from collections.abc import Generator
from pathlib import Path

from snektest import fixture, load_fixture, test

SECOND_LOADED = Path({str(second_loaded)!r})

@fixture(scope="run")
def first_descriptor() -> Generator[str]:
    yield "first"

@fixture(scope="run")
def second_descriptor() -> Generator[str]:
    yield "second"

@test()
def test_first() -> None:
    _ = load_fixture(first_descriptor())
    deadline = time.monotonic() + 2
    while not SECOND_LOADED.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not SECOND_LOADED.exists():
        raise RuntimeError("second test did not resume")

@test()
def test_second() -> None:
    _ = load_fixture(second_descriptor())
    _ = SECOND_LOADED.write_text("ready")
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "2", timeout=10)

    assert_eq(result["returncode"], 0)
    assert_eq(result["passed"], 2)


@test(mark="slow")
def test_async_run_fixture_setup_is_bounded_and_unrelated_test_continues() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_async_run_timeout.py"
        _ = test_file.write_text(
            """
import asyncio
from collections.abc import AsyncGenerator

from snektest import fixture, load_fixture, test

@fixture(scope="run")
async def hanging_descriptor() -> AsyncGenerator[str]:
    await asyncio.Event().wait()
    yield "unreachable"

@test()
async def test_hanging_descriptor() -> None:
    _ = await load_fixture(hanging_descriptor())

@test()
def test_unrelated() -> None:
    pass
""".lstrip()
        )

        result = run_test_subprocess(
            test_file,
            "--workers",
            "2",
            "--timeout",
            "0.5",
            timeout=10,
        )

    assert_eq(result["returncode"], 1)
    assert_eq(result["errors"], 1)
    assert_eq(result["passed"], 1)
    assert_in("publication failed", result["tests"][0]["exception"]["message"])


@test(mark="slow")
def test_mutex_prevents_overlap_without_blocking_unrelated_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        events_file = directory / "events.jsonl"
        test_file = directory / "test_mutex.py"
        _ = test_file.write_text(
            f"""
import json
import time
from pathlib import Path

from snektest import test

EVENTS = Path({str(events_file)!r})

def record(event: str, name: str) -> None:
    with EVENTS.open("a") as output:
        _ = output.write(json.dumps({{"event": event, "name": name, "at": time.monotonic()}}) + "\\n")

def work(name: str) -> None:
    record("start", name)
    time.sleep(0.1)
    record("end", name)

@test(mutex="shared")
def test_first() -> None:
    work("first")

@test(mutex="shared")
def test_second() -> None:
    work("second")

@test()
def test_unrelated() -> None:
    work("unrelated")
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "2", timeout=10)
        events = [json.loads(line) for line in events_file.read_text().splitlines()]

    times = {(entry["name"], entry["event"]): entry["at"] for entry in events}
    assert_eq(result["returncode"], 0)
    assert_eq(
        times[("unrelated", "start")] < times[("first", "end")],
        True,
    )
    assert_eq(
        times[("second", "start")] < times[("first", "end")],
        False,
    )


@test(mark="slow")
def test_worker_crash_is_reported_and_replaced_for_remaining_cases() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        sentinel = directory / "crashed"
        test_file = directory / "test_crash.py"
        _ = test_file.write_text(
            f"""
import os
from pathlib import Path

from snektest import test

SENTINEL = Path({str(sentinel)!r})

@test()
def test_crash() -> None:
    if not SENTINEL.exists():
        _ = SENTINEL.write_text("crashed")
        os._exit(7)

@test()
def test_after_crash() -> None:
    pass
""".lstrip()
        )

        result = run_test_subprocess(test_file, "--workers", "1", timeout=10)

    assert_eq(result["returncode"], 1)
    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 1)
    assert_in("exited unexpectedly", result["tests"][0]["exception"]["message"])


@test(mark="slow")
def test_repeated_filter_runs_as_distinct_ordinals() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_repeated.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )
        command = [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            "--workers",
            "2",
            str(test_file),
            str(test_file),
        ]

        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(completed.stdout)

    assert_eq(completed.returncode, 0)
    assert_eq(result["passed"], 2)
    assert_eq(len(result["tests"]), 2)
