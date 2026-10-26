"""Real native-checker process cleanup under operating-system backpressure."""

import os
import subprocess
import sys
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, skip, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test(mark="slow")
def test_report_limit_drains_full_native_pipes_before_reaping() -> None:
    if sys.platform != "linux":
        skip("This backpressure fixture requires Linux procfs and pipe-size controls")
    directory = load_fixture(tmp_dir_fixture())
    scratch = directory / "native-pipe-scratch"
    scratch.mkdir()
    source = (
        "from snektest import test, test_typing\n"
        "def takes_int(value: int) -> int:\n    return value\n"
        "@test_typing\ndef consumer() -> None:\n"
        + '    takes_int("wrong")\n' * 20_000
        + "@test(mark='fast')\ndef following() -> None:\n    pass\n"
    )
    path = create_test_file(directory, source, name="test_native_backpressure")
    driver = directory / "native_pipe_driver.py"
    driver.write_text(
        dedent("""
        import asyncio
        from array import array
        import fcntl
        import os
        from pathlib import Path
        import stat
        import sys
        import termios
        import time
        from snektest import assert_eq, assert_in, assert_isinstance, assert_true
        from snektest.cli import run_tests_programmatic
        from snektest.models import ErrorResult, FilterItem

        capacity = 1048576
        read_fd, write_fd = os.pipe()
        try:
            try:
                fcntl.fcntl(read_fd, fcntl.F_SETPIPE_SZ, capacity)
            except PermissionError:
                print("pipe growth unavailable")
                sys.exit(0)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        baseline: set[int] = set()
        filled = False

        def fill_pipe(fd: int) -> None:
            global filled
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                queued = array("i", [0])
                fcntl.ioctl(fd, termios.FIONREAD, queued, True)
                if queued[0] >= capacity:
                    filled = True
                    return
                time.sleep(0.001)
            raise RuntimeError("native checker did not fill its enlarged stdout pipe")

        def observe(event: str, arguments: tuple[object, ...]) -> None:
            if event != "subprocess.Popen" or Path(str(arguments[0])).name not in ("ty", "ty.exe"):
                return
            readers: list[int] = []
            for entry in Path("/proc/self/fd").iterdir():
                fd = int(entry.name)
                if fd in baseline:
                    continue
                try:
                    if stat.S_ISFIFO(os.fstat(fd).st_mode):
                        fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, capacity)
                        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                            readers.append(fd)
                except OSError:
                    continue
            assert_true(bool(readers))
            # Observe real ty output before allowing asyncio to consume it.
            # The bytes are produced solely by the unmodified native checker.
            asyncio.get_running_loop().call_soon(fill_pipe, min(readers))

        async def main() -> None:
            global baseline
            baseline = set()
            for entry in Path("/proc/self/fd").iterdir():
                try:
                    if stat.S_ISFIFO(os.fstat(int(entry.name)).st_mode):
                        baseline.add(int(entry.name))
                except OSError:
                    pass
            sys.addaudithook(observe)
            result = await run_tests_programmatic([FilterItem(sys.argv[1])])
            assert_true(filled)
            assert_eq((result.errors, result.failed, result.passed), (1, 0, 1))
            error = assert_isinstance(result.test_results[0].result, ErrorResult)
            assert_in("4 MiB report limit", error.exception.message)
            remaining: list[int] = []
            for entry in Path("/proc/self/fd").iterdir():
                fd = int(entry.name)
                try:
                    if fd not in baseline and stat.S_ISFIFO(os.fstat(fd).st_mode):
                        remaining.append(fd)
                except OSError:
                    pass
            assert_eq(remaining, [])
            print("full pipes drained")

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
    if "pipe growth unavailable" in completed.stdout:
        skip("The kernel does not permit enlarging owned pipes")
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("full pipes drained", completed.stdout)
    assert_eq(list(scratch.iterdir()), [])


@test(
    [
        Param("timeout", name="timeout"),
        Param("cancel", name="cancel"),
        Param("terminate", name="terminate"),
    ],
    mark="slow",
)
def test_active_native_checker_is_reaped_and_cannot_certify_rejection(
    mode: str,
) -> None:
    if sys.platform != "linux":
        skip("This independent process audit requires Linux procfs")
    directory = load_fixture(tmp_dir_fixture())
    scratch = directory / f"native-{mode}-scratch"
    scratch.mkdir()
    path = create_test_file(
        directory,
        "from snektest import test_typing\n"
        "def takes_int(value: int) -> int:\n    return value\n"
        "@test_typing\ndef consumer() -> None:\n" + '    takes_int("wrong")\n' * 5_000,
        name=f"test_native_{mode}",
    )
    driver = directory / "native_lifecycle_driver.py"
    driver.write_text(
        dedent("""
        import asyncio
        import os
        from pathlib import Path
        import signal
        import sys
        from snektest import assert_eq, assert_in, assert_is_not_none, assert_isinstance, assert_raises, fail
        from snektest.cli import run_tests_programmatic
        from snektest.models import ErrorResult, FilterItem

        mode = sys.argv[2]
        child: int | None = None

        def control(task: asyncio.Task[object]) -> None:
            global child
            children = Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
            assert_eq(len(children), 1)
            child = int(children[0])
            if mode == "terminate":
                os.kill(child, signal.SIGTERM)
            else:
                os.kill(child, signal.SIGSTOP)
                if mode == "cancel":
                    loop = asyncio.get_running_loop()
                    loop.call_later(0.05, task.cancel)
                    loop.call_later(0.05, task.cancel)

        def observe(event: str, arguments: tuple[object, ...]) -> None:
            if event == "subprocess.Popen" and Path(str(arguments[0])).name in ("ty", "ty.exe"):
                task = asyncio.current_task()
                if task is not None:
                    asyncio.get_running_loop().call_soon(control, task)

        async def main() -> None:
            baseline = set(asyncio.all_tasks())
            sys.addaudithook(observe)
            run = asyncio.create_task(run_tests_programmatic([FilterItem(sys.argv[1])], timeout=1))
            try:
                result = await run
            except asyncio.CancelledError:
                assert_eq(mode, "cancel")
            else:
                if mode == "cancel":
                    fail("parent cancellation did not propagate")
                assert_eq((result.errors, result.failed, result.passed), (1, 0, 0))
                error = assert_isinstance(result.test_results[0].result, ErrorResult)
                if mode == "timeout":
                    assert_eq(error.exception.type_name, "TestTimeoutError")
                else:
                    assert_in("ty invocation failed with status", error.exception.message)
            pid = assert_is_not_none(child)
            with assert_raises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
            assert_eq([task.get_name() for task in asyncio.all_tasks() - baseline], [])
            print("native child reaped")

        asyncio.run(main())
        """),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(driver), str(path), mode],
        env=dict(os.environ, TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch)),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("native child reaped", completed.stdout)
    assert_eq(list(scratch.iterdir()), [])
