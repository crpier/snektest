"""Native Trio observation, replacement, temporary ownership and signal seams."""

lazy import json
lazy import os
lazy import signal
lazy import subprocess
lazy import sys
lazy import time
lazy from pathlib import Path
lazy from textwrap import dedent

lazy from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    assert_true,
    skip,
    temporary_directory,
    test,
)
lazy from testutils.helpers import create_test_file

_WORKERS: list[Param[int | None]] = [
    Param(value=None, name="local"),
    Param(value=1, name="worker"),
]


def _command(path: Path, workers: int | None) -> list[str]:
    command = [sys.executable, "-m", "snektest", "--backend", "trio", "--json-output"]
    if workers is not None:
        command.extend(("--workers", str(workers)))
    command.append(str(path))
    return command


@test(_WORKERS, mark="slow")
def test_native_progress_and_temporary_lifetimes(workers: int | None) -> None:
    directory = temporary_directory()
    base = directory / "owned"
    base.mkdir()
    path = create_test_file(
        directory,
        dedent(f"""
        from collections.abc import AsyncGenerator
        from pathlib import Path
        import logging, warnings, trio
        from snektest import assert_eq, assert_true, capture_logs, fixture, load_fixture, temporary_directory, test
        @fixture
        async def resource() -> AsyncGenerator[Path]:
            task = trio.lowlevel.current_task()
            root = temporary_directory(base=Path({str(base)!r}))
            try:
                yield root
            finally:
                assert_true(root.is_dir())
                assert_eq(trio.lowlevel.current_task(), task)
        @test(mark="medium")
        async def test_observed() -> None:
            assert_true((await load_fixture(resource())).is_dir())
            with capture_logs("native.observed") as logs:
                logging.getLogger("native.observed").info("PRIVATE_NATIVE_MESSAGE")
                assert_eq(logs.messages, ("PRIVATE_NATIVE_MESSAGE",))
            warnings.warn("PRIVATE_NATIVE_WARNING", UserWarning)
            await trio.sleep(0)
    """),
    )
    progress = directory / "progress.jsonl"
    command = _command(path, workers)
    command[3:3] = ["--progress-output", str(progress), "--progress-interval", "0.05"]
    completed = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, check=False, timeout=15
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    result = json.loads(completed.stdout)
    assert_eq(result["passed"], 1)
    text = progress.read_text()
    assert_false("PRIVATE_NATIVE" in text)
    records = [json.loads(line) for line in text.splitlines()]
    events = {record["event"] for record in records}
    for event in (
        "run_started",
        "run_finished",
        "body_started",
        "fixture_setup_started",
        "fixture_teardown_finished",
    ):
        assert_in(event, events)
    assert_eq(records[-1]["event"], "run_finished")
    assert_eq(records[-1]["status"], "completed")
    assert_eq(list(base.iterdir()), [])


@test(mark="slow")
def test_native_replacement_replays_run_descriptors_and_observer_identity() -> None:
    directory = temporary_directory()
    first_pid = directory / "pid"
    trace = directory / "host-trace"
    path = create_test_file(
        directory,
        dedent(f"""
        import os, trio
        from collections.abc import AsyncGenerator
        from pathlib import Path
        from snektest import assert_eq, assert_ne, fixture, load_fixture, test
        trace = Path({str(trace)!r})
        pid = Path({str(first_pid)!r})
        @fixture(scope="run")
        async def resource() -> AsyncGenerator[int]:
            task = trio.lowlevel.current_task()
            _ = trace.write_text("setup\\n")
            with trio.CancelScope():
                yield 42
                assert_eq(trio.lowlevel.current_task(), task)
                with trace.open("a") as stream:
                    _ = stream.write("teardown\\n")
        @test(mark="medium")
        async def test_first() -> None:
            assert_eq(await load_fixture(resource()), 42)
            _ = pid.write_text(str(os.getpid()))
        @test(mark="slow")
        def test_crash() -> None:
            os._exit(9)
        @test(mark="medium")
        async def test_replacement() -> None:
            assert_ne(os.getpid(), int(pid.read_text()))
            assert_eq(await load_fixture(resource()), 42)
            await trio.sleep(0)
    """),
    )
    progress = directory / "progress.jsonl"
    command = _command(path, 1)
    command[3:3] = ["--progress-output", str(progress)]
    completed = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, check=False, timeout=15
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    result = json.loads(completed.stdout)
    assert_eq((result["passed"], result["errors"]), (2, 1))
    assert_eq(trace.read_text().splitlines(), ["setup", "teardown"])
    records = [json.loads(line) for line in progress.read_text().splitlines()]
    workers = {
        record["pid"]
        for record in records
        if record["event"] == "process_started"
        and record["worker"].startswith("snektest-worker-")
    }
    assert_eq(len(workers), 2)
    assert_eq(records[-1]["status"], "failed")


@test(_WORKERS, mark="slow")
def test_native_external_sigint_has_one_interruption_document(
    workers: int | None,
) -> None:
    if os.name != "posix":
        skip("POSIX external signal delivery; Windows needs native console coverage")
    directory = temporary_directory()
    ready, cleaned = directory / "ready", directory / "cleaned"
    path = create_test_file(
        directory,
        dedent(f"""
        from collections.abc import AsyncGenerator
        from pathlib import Path
        import trio
        from snektest import fixture, load_fixture, test
        @fixture
        async def resource() -> AsyncGenerator[None]:
            try:
                yield
            finally:
                _ = Path({str(cleaned)!r}).write_text("cleaned")
        @test(mark="medium")
        async def test_wait() -> None:
            _ = await load_fixture(resource())
            _ = Path({str(ready)!r}).write_text("ready")
            await trio.sleep_forever()
    """),
    )
    # Establish launcher intent even when this test itself is a shell background job.
    launcher = "import os,signal,sys; signal.signal(signal.SIGINT,signal.SIG_DFL); os.execv(sys.executable,[sys.executable,*sys.argv[1:]])"
    command = [sys.executable, "-c", launcher, *_command(path, workers)[1:]]
    with subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            assert_true(ready.exists())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                _ = process.communicate(timeout=5)
    assert_eq(process.returncode, 2, msg=stdout + stderr)
    assert_eq(stderr, "")
    assert_eq(json.loads(stdout)["error"]["category"], "interrupted")
    if workers is None:
        assert_eq(cleaned.read_text(), "cleaned")


@test(_WORKERS, mark="slow")
def test_native_runtime_preserves_inherited_ignored_signals(
    workers: int | None,
) -> None:
    if os.name != "posix":
        skip("POSIX ignored signal dispositions")
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        import signal, trio
        from snektest import assert_eq, test
        @test(mark="medium")
        async def test_ignored() -> None:
            for signum in (signal.SIGINT, signal.SIGQUIT, signal.SIGHUP):
                assert_eq(signal.getsignal(signum), signal.SIG_IGN)
                signal.raise_signal(signum)
            await trio.sleep(0)
    """),
    )
    launcher = "import os,signal,sys; [signal.signal(s,signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGQUIT,signal.SIGHUP)]; os.execv(sys.executable,[sys.executable,*sys.argv[1:]])"
    completed = subprocess.run(
        [sys.executable, "-c", launcher, *_command(path, workers)[1:]],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(json.loads(completed.stdout)["passed"], 1)
