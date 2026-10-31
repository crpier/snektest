"""Lifecycle observation through real, independently controlled CLI processes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent
from typing import Any

from snektest import (
    Param,
    assert_eq,
    assert_in,
    assert_not_in,
    skip,
    temporary_directory,
    test,
)
from testutils.helpers import create_test_file

_WORKERS: list[Param[tuple[str, ...]]] = [
    Param(value=(), name="local"),
    Param(value=("--workers", "1"), name="worker"),
]


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    # A live writer can be between write and flush. Only complete lines count.
    text = path.read_text(encoding="utf-8")
    return [
        json.loads(line)
        for line in text.splitlines(keepends=True)
        if line.endswith("\n")
    ]


def _wait_for(
    path: Path, process: subprocess.Popen[str], event: str, *, phase: str | None = None
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        records = _records(path)
        for record in records:
            if record["event"] != event:
                continue
            active = {item["phase"] for item in record.get("active", [])}
            if phase == "body" and "fixture_setup" in active:
                continue
            if phase is None or phase in active:
                return records
        if process.poll() is not None:
            break
        time.sleep(0.01)
    assert_eq(True, False, msg=f"Did not observe {event}/{phase}: {_records(path)!r}")
    return []


@test(_WORKERS, mark="slow")
def test_progress_identifies_live_import_setup_body_and_teardown(
    workers: tuple[str, ...],
) -> None:
    directory = temporary_directory()
    events = directory / "live.jsonl"
    gates = [
        directory / f"gate-{stage}" for stage in ("import", "setup", "body", "teardown")
    ]
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio, time
        from collections.abc import AsyncGenerator
        from pathlib import Path
        from snektest import fixture, load_fixture, test

        while not Path({str(gates[0])!r}).exists():
            time.sleep(0.01)

        async def wait_gate(path: str) -> None:
            while not Path(path).exists():
                await asyncio.sleep(0.01)

        @fixture
        async def resource() -> AsyncGenerator[None]:
            await wait_gate({str(gates[1])!r})
            yield None
            await wait_gate({str(gates[3])!r})

        @test(mark='slow')
        async def test_live() -> None:
            _ = await load_fixture(resource())
            print('secret captured user output')
            await wait_gate({str(gates[2])!r})
        """),
        name="test_live_progress",
    )
    selector = f"{path}::test_live"
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            "--progress-interval",
            "0.05",
            *workers,
            selector,
            selector,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            for gate, phase in zip(
                gates,
                ("import", "fixture_setup", "body", "fixture_teardown"),
                strict=True,
            ):
                records = _wait_for(events, process, "still_running", phase=phase)
                if phase != "import":
                    starts = [
                        item for item in records if item["event"] == "test_started"
                    ]
                    assert_eq(starts[0]["selector"], selector)
                    assert_eq(starts[0]["ordinal"], 0)
                    assert_eq(starts[0]["attempt"], 1)
                gate.touch()
            stdout, stderr = process.communicate(timeout=20)
        finally:
            for gate in gates:
                gate.touch()
            if process.poll() is None:
                process.kill()
                _ = process.communicate(timeout=10)
    assert_eq(process.returncode, 0, msg=stdout + stderr)
    assert_eq(stderr, "")
    document = json.loads(stdout)
    assert_eq(document["passed"], 2)
    assert_eq(
        [case["captured_output"] for case in document["tests"]],
        ["secret captured user output\n"] * 2,
    )
    records = _records(events)
    assert_eq(records[0]["event"], "run_started")
    assert_eq(records[-1]["event"], "run_finished")
    assert_eq(records[-1]["status"], "completed")
    assert_eq(records[-1]["truncated"], False)
    assert_eq(len({record["run_id"] for record in records}), 1)
    for event in ("test_started", "test_finished"):
        cases = [record for record in records if record["event"] == event]
        assert_eq([case["ordinal"] for case in cases], [0, 1])
        assert_eq([case["selector"] for case in cases], [selector, selector])
    for pid in {record["pid"] for record in records}:
        per_process = [record for record in records if record["pid"] == pid]
        elapsed = [record["elapsed"] for record in per_process]
        assert_eq(elapsed, sorted(elapsed))
        assert_eq(
            [record["sequence"] for record in per_process],
            list(range(1, len(per_process) + 1)),
        )
    assert_not_in("secret captured user output", events.read_text())
    assert_in("fixture_setup_started", [record["event"] for record in records])
    fixtures = [
        record for record in records if record["event"] == "fixture_teardown_started"
    ]
    assert_eq([record["scope"] for record in fixtures], ["function", "function"])
    assert_eq([record["fixture"] for record in fixtures], ["resource", "resource"])
    if workers:
        assert_in("snektest-worker-1", {record["worker"] for record in records})
        assert_in("snektest-fixture-host", {record["worker"] for record in records})


@test(_WORKERS, mark="slow")
def test_progress_reports_session_and_run_fixture_scopes(
    workers: tuple[str, ...],
) -> None:
    directory = temporary_directory()
    events = directory / "scopes.jsonl"
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test

        @fixture(scope='run')
        async def shared() -> AsyncGenerator[int]:
            yield 1

        @fixture(scope='session')
        async def session() -> AsyncGenerator[int]:
            yield await load_fixture(shared())

        @test(mark='slow')
        async def test_scopes() -> None:
            _ = await load_fixture(session())
        """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            *workers,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)
    records = _records(events)
    for phase in ("fixture_setup_started", "fixture_teardown_started"):
        assert_eq(
            {record["scope"] for record in records if record["event"] == phase},
            {"session", "run"},
        )
    teardown = [
        record for record in records if record["event"] == "fixture_teardown_started"
    ]
    assert_eq([record["ordinal"] for record in teardown], [None, None])
    assert_eq(records[-1]["event"], "run_finished")


@test(
    [
        Param(value=("pass", 0, "completed"), name="success"),
        Param(value=("assert_eq(1, 2)", 1, "failed"), name="failure"),
        Param(
            value=("raise RuntimeError('private failure text')", 1, "failed"),
            name="error",
        ),
    ],
    _WORKERS,
    mark="slow",
)
def test_progress_completion_retains_result_status(
    outcome: tuple[str, int, str], workers: tuple[str, ...]
) -> None:
    body, exit_code, status = outcome
    directory = temporary_directory()
    events = directory / "outcome.jsonl"
    path = create_test_file(
        directory,
        f"from snektest import assert_eq, test\n@test(mark='fast')\ndef test_outcome() -> None:\n    {body}\n",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            *workers,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert_eq(completed.returncode, exit_code, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["exit_code"], exit_code)
    records = _records(events)
    assert_eq(records[-1]["event"], "run_finished")
    assert_eq(records[-1]["status"], status)
    assert_eq(records[-1]["exit_code"], exit_code)
    assert_not_in("private failure text", events.read_text())


@test(
    _WORKERS,
    [
        Param(value="body", name="body"),
        Param(value="fixture_setup", name="setup"),
        Param(value="fixture_teardown", name="teardown"),
    ],
    mark="slow",
)
def test_progress_interruption_and_hard_kill_are_distinguishable(
    workers: tuple[str, ...],
    phase: str,
) -> None:
    if os.name == "nt":
        skip("POSIX external SIGINT delivery")
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio
        from collections.abc import AsyncGenerator
        from snektest import fixture, load_fixture, test
        @fixture
        async def resource() -> AsyncGenerator[None]:
            if {phase!r} == 'fixture_setup':
                await asyncio.Event().wait()
            yield None
            if {phase!r} == 'fixture_teardown':
                await asyncio.Event().wait()
        @test(mark='slow')
        async def test_wait() -> None:
            _ = await load_fixture(resource())
            if {phase!r} == 'body':
                await asyncio.Event().wait()
        """),
    )
    for hard in (False, True):
        # Do not hard-kill a coordinator with live execution children: arbitrary
        # process-tree containment is intentionally outside this contract.
        if hard and (workers or phase != "body"):
            continue
        events = directory / f"interrupt-{hard}.jsonl"
        launcher = "import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execv(sys.executable, [sys.executable, '-m', 'snektest', *sys.argv[1:]])"
        with subprocess.Popen(
            [
                sys.executable,
                "-c",
                launcher,
                "--json-output",
                "--progress-output",
                str(events),
                "--progress-interval",
                "0.05",
                *workers,
                "--timeout",
                "1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                _ = _wait_for(events, process, "still_running", phase=phase)
                process.send_signal(signal.SIGKILL if hard else signal.SIGINT)
                stdout, stderr = process.communicate(timeout=20)
            finally:
                if process.poll() is None:
                    process.kill()
                    _ = process.communicate(timeout=10)
        records = _records(events)
        if hard:
            assert_not_in("run_finished", [record["event"] for record in records])
        else:
            assert_eq(process.returncode, 2, msg=stdout + stderr)
            assert_eq(json.loads(stdout)["error"]["category"], "interrupted")
            assert_eq(records[-1]["event"], "run_finished")
            assert_eq(records[-1]["status"], "interrupted")


@test(mark="slow")
def test_progress_collect_only_collection_failure_and_existing_file() -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        "from snektest import test\n@test(mark='fast')\ndef test_ok() -> None:\n    pass\n",
    )
    for collect in (False, True):
        events = directory / f"collection-{collect}.jsonl"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "snektest",
                "--json-output",
                "--progress-output",
                str(events),
                *(["--collect-only"] if collect else []),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
        assert_eq(
            json.loads(completed.stdout)["kind"],
            "collection" if collect else "test_run",
        )
        assert_eq(_records(events)[-1]["event"], "run_finished")
        original = events.read_bytes()
        repeated = subprocess.run(
            [
                sys.executable,
                "-m",
                "snektest",
                "--json-output",
                "--progress-output",
                str(events),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert_eq(repeated.returncode, 2)
        assert_eq(json.loads(repeated.stdout)["kind"], "error")
        assert_eq(events.read_bytes(), original)
    path.write_text("raise RuntimeError('private import failure')\n")
    events = directory / "collection-error.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["error"]["category"], "collection")
    assert_eq(_records(events)[-1]["status"], "error")
    assert_not_in("private import failure", events.read_text())


@test(_WORKERS, mark="slow")
def test_progress_does_not_capture_descriptor_output_and_observes_replacement(
    workers: tuple[str, ...],
) -> None:
    directory = temporary_directory()
    events = directory / "replacement.jsonl"
    path = create_test_file(
        directory,
        dedent("""
        import os
        from snektest import test
        @test(mark='slow')
        def test_first() -> None:
            print('private Python output', flush=True)
            _ = os.write(1, b'private descriptor output\\n')
        @test(mark='slow')
        def test_crash() -> None:
            os._exit(17)
        @test(mark='slow')
        def test_after() -> None:
            pass
        """),
    )
    # A local os._exit deliberately cannot emit a terminal event. In workers,
    # replacement must have a distinct incarnation and retain occurrence IDs.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "-s",
            "--progress-output",
            str(events),
            *workers,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    records = _records(events)
    assert_not_in("private Python output", events.read_text())
    assert_not_in("private descriptor output", events.read_text())
    if not workers:
        assert_eq(completed.returncode, 17)
        assert_not_in("run_finished", [record["event"] for record in records])
        return
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(document["passed"], 2)
    assert_eq(document["errors"], 1)
    assert_in("private Python output", document["uncaptured_output"])
    assert_in("private descriptor output", document["uncaptured_output"])
    starts = [record for record in records if record["event"] == "test_started"]
    assert_eq([record["ordinal"] for record in starts], [0, 1, 2])
    assert_eq(starts[0]["worker"], "snektest-worker-1")
    assert_eq(starts[-1]["worker"], "snektest-worker-2")
    assert_eq(records[-1]["event"], "run_finished")
    assert_eq(records[-1]["status"], "failed")
    missing_process = starts[1]["pid"]
    assert_eq(
        any(
            record["event"] == "process_finished" and record["pid"] == missing_process
            for record in records
        ),
        False,
    )


@test(
    [
        Param(value=("--progress-interval", "nan"), name="nan"),
        Param(value=("--progress-interval", "inf"), name="infinity"),
        Param(value=("--progress-interval", "0"), name="zero"),
        Param(value=("--progress-interval", "text"), name="not-seconds"),
        Param(value=("--progress-output", "-"), name="stdout"),
        Param(value=("--progress-output",), name="missing-path"),
    ],
    mark="slow",
)
def test_progress_rejects_invalid_options(arguments: tuple[str, ...]) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", "--json-output", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["error"]["category"], "usage")


@test(_WORKERS, mark="slow")
def test_progress_nested_runners_do_not_reuse_command_occurrence_identities(
    workers: tuple[str, ...],
) -> None:
    directory = temporary_directory()
    events = directory / "nested.jsonl"
    inner = create_test_file(
        directory,
        "from snektest import test\n@test(mark='fast')\ndef test_inner() -> None:\n    pass\n",
        name="test_nested_inner",
    )
    outer = create_test_file(
        directory,
        dedent(f"""
        from snektest import assert_eq, test
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem
        @test(mark='slow')
        async def test_outer() -> None:
            result = await run_tests_programmatic([FilterItem({str(inner)!r})])
            assert_eq(result.passed, 1)
        """),
        name="test_nested_outer",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            *workers,
            str(outer),
            str(outer),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 2)
    starts = [
        record for record in _records(events) if record["event"] == "test_started"
    ]
    assert_eq([record["ordinal"] for record in starts], [0, 1])
    assert_eq([record["selector"] for record in starts], [f"{outer}::test_outer"] * 2)


@test(_WORKERS, mark="slow")
def test_progress_accepts_large_finite_intervals_without_observer_failure(
    workers: tuple[str, ...],
) -> None:
    directory = temporary_directory()
    events = directory / "large-interval.jsonl"
    path = create_test_file(
        directory,
        "from snektest import test\n@test(mark='fast')\ndef test_ok() -> None:\n    pass\n",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            "--progress-interval",
            "1e100",
            *workers,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(json.loads(completed.stdout)["passed"], 1)
    assert_eq(_records(events)[-1]["event"], "run_finished")


@test(mark="slow")
def test_progress_write_failure_does_not_replace_or_duplicate_final_json() -> None:
    if os.name == "nt":
        skip("POSIX resource limit supplies a real append/close IO failure")
    directory = temporary_directory()
    events = directory / "write-failure.jsonl"
    path = create_test_file(
        directory,
        dedent("""
        import atexit, resource, signal
        from snektest import test
        @test(mark='slow')
        def test_disk_limit() -> None:
            original = resource.getrlimit(resource.RLIMIT_FSIZE)
            # Restore before coverage/interpreter exit callbacks write files.
            _ = atexit.register(resource.setrlimit, resource.RLIMIT_FSIZE, original)
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, original[1]))
        """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)
    assert_eq(completed.stdout.count("\n"), 1)
    assert_in("Lifecycle progress output failed", completed.stderr)
    assert_not_in("run_finished", [record["event"] for record in _records(events)])


@test(mark="slow")
def test_progress_output_is_bounded_and_explicitly_marks_truncation() -> None:
    directory = temporary_directory()
    events = directory / "bounded.jsonl"
    path = create_test_file(
        directory,
        dedent("""
        from snektest import Param, test
        CASES = [Param(value=i, name='long-' + 'x' * 1024 + str(i)) for i in range(5000)]
        @test(CASES, mark='fast')
        def test_many(value: int) -> None:
            pass
        """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--progress-output",
            str(events),
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=60,
    )
    assert_eq(completed.returncode, 0, msg=completed.stderr)
    assert_eq(events.stat().st_size <= 16 * 1024 * 1024 + 2048, True)
    records = _records(events)
    assert_eq(sum(record["event"] == "stream_truncated" for record in records), 1)
    assert_eq(records[-1]["event"], "run_finished")
    assert_eq(records[-1]["truncated"], True)
    assert_eq(any(record.get("selector_truncated") for record in records), True)
