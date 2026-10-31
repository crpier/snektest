"""Standalone startup policy through real CLI and embedding processes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, skip, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test(
    [
        Param(value="snektest", name="module"),
        Param(value="installed", name="executable"),
        Param(value="snektest.cli", name="legacy-module"),
    ],
    mark="slow",
)
def test_cli_enables_context_aware_warnings_before_collection(entry: str) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import sys
        from snektest import assert_eq, test

        assert_eq(sys.flags.context_aware_warnings, 1)

        @test(mark="fast")
        def enabled_in_body() -> None:
            assert_eq(sys.flags.context_aware_warnings, 1)
        """),
    )
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    command = (
        [
            str(
                Path(sys.executable).with_name(
                    "snektest.exe" if os.name == "nt" else "snektest"
                )
            )
        ]
        if entry == "installed"
        else [sys.executable, "-m", entry]
    )
    completed = subprocess.run(
        [*command, "--json-output", str(path)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)


_POLICIES: list[Param[tuple[tuple[str, ...], str, int]]] = [
    Param(value=((), "0", 0), name="environment-opt-out"),
    Param(value=((), "1", 1), name="environment-enabled"),
    Param(value=(("-X", "context_aware_warnings=0"), "1", 0), name="explicit-off-wins"),
    Param(value=(("-X", "context_aware_warnings=1"), "0", 1), name="explicit-on-wins"),
    Param(value=(("-E",), "0", 1), name="ignored-environment"),
    Param(value=(("-I",), "0", 1), name="isolated"),
]


@test(_POLICIES, mark="slow")
def test_cli_honors_python_startup_precedence(
    policy: tuple[tuple[str, ...], str, int],
) -> None:
    arguments, environment_value, expected = policy
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import sys
        from snektest import assert_eq, test

        assert_eq(sys.flags.context_aware_warnings, {expected})

        @test(mark="fast")
        def selected_policy() -> None:
            assert_eq(sys.flags.context_aware_warnings, {expected})
        """),
        name=f"test_startup_policy_{expected}",
    )
    environment = dict(os.environ)
    environment["PYTHON_CONTEXT_AWARE_WARNINGS"] = environment_value
    completed = subprocess.run(
        [sys.executable, *arguments, "-m", "snektest", "--json-output", str(path)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)


@test(mark="slow")
def test_library_import_and_programmatic_run_leave_host_policy_unchanged() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent("""
        import sys
        from snektest import assert_eq, test
        assert_eq(sys.flags.context_aware_warnings, 0)

        @test(mark="fast")
        def original_policy() -> None:
            assert_eq(sys.flags.context_aware_warnings, 0)
        """),
        name="test_embedded_policy",
    )
    source = dedent(f"""
        import asyncio, contextlib, io, os, signal, sys
        handler = lambda signum, frame: None
        signal.signal(signal.SIGINT, handler)
        original = os.environ.get('PYTHON_CONTEXT_AWARE_WARNINGS')
        from snektest import assert_eq
        from snektest.cli import main, run_tests_programmatic
        from snektest.models import FilterItem
        assert_eq(sys.flags.context_aware_warnings, 0)
        result = asyncio.run(run_tests_programmatic([FilterItem({str(path)!r})]))
        assert_eq(result.passed, 1)
        sys.argv = ['snektest', '--version']
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                main()
            except SystemExit as error:
                assert_eq(error.code, 0)
        assert_eq(sys.flags.context_aware_warnings, 0)
        assert_eq(os.environ.get('PYTHON_CONTEXT_AWARE_WARNINGS'), original)
        assert_eq(signal.getsignal(signal.SIGINT), handler)
        print('embedding unchanged')
    """)
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stdout, "embedding unchanged\n")


@test(mark="slow")
def test_cli_preserves_interpreter_arguments_cwd_streams_and_repeated_selectors() -> (
    None
):
    directory = load_fixture(tmp_dir_fixture()) / "startup space 雪"
    directory.mkdir(exist_ok=True)
    imported = directory / "imports.txt"
    _ = imported.write_text("")
    path = create_test_file(
        directory,
        dedent(f"""
        import os, sys
        from pathlib import Path
        from snektest import Param, assert_eq, assert_in, test

        with Path({str(imported)!r}).open('a') as stream:
            _ = stream.write('imported\\n')

        @test([Param('value', 'left case')], [Param(2, 'right case')], mark='fast')
        def test_arguments(value: str, count: int) -> None:
            assert_eq(value, 'value')
            assert_eq(count, 2)
            assert_eq(sys.flags.context_aware_warnings, 1)
            assert_eq(sys.flags.optimize, 1)
            assert_eq(sys.flags.dont_write_bytecode, 1)
            assert_eq(sys.flags.utf8_mode, 0)
            assert_eq(sys.flags.int_max_str_digits, 6000)
            assert_eq(sys.executable, {sys.executable!r})
            assert_eq(Path.cwd(), Path({str(directory)!r}))
            assert_in('error::UserWarning', sys.warnoptions)
            print('captured Python output')
            assert_eq(sys.stdin.readline().strip(), 'input [with spaces]')
            _ = os.write(1, b'descriptor stdout\\n')
            _ = os.write(2, b'descriptor stderr\\n')
        """),
        name="test_argument_boundaries",
    )
    selector = f"{path}::test_arguments[left case, right case]"
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-O",
            "-B",
            "-X",
            "utf8=0",
            "-X",
            "int_max_str_digits=6000",
            "-X",
            "lazy_imports=all",
            "-W",
            "error::UserWarning",
            "-m",
            "snektest",
            "--json-output",
            "-k",
            "(arguments and fast) or unmatched",
            selector,
            selector,
        ],
        cwd=directory,
        input="input [with spaces]\ninput [with spaces]\n",
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(document["passed"], 2)
    assert_eq(completed.stdout.count("\n"), 1)
    assert_eq(completed.stderr, "")
    assert_eq(document["uncaptured_output"].count("descriptor stdout\n"), 2)
    assert_eq(document["uncaptured_output"].count("descriptor stderr\n"), 2)
    assert_eq(
        [case["captured_output"] for case in document["tests"]],
        ["captured Python output\n"] * 2,
        msg=repr(document),
    )
    assert_eq(imported.read_text(), "imported\n")


@test(mark="slow")
def test_explicitly_enabled_cli_does_not_repeat_startup_code() -> None:
    directory = load_fixture(tmp_dir_fixture())
    marker = directory / "startup-count.txt"
    _ = marker.write_text("")
    script = directory / "invoke_cli.py"
    _ = script.write_text(
        dedent(f"""
        import runpy, sys
        from pathlib import Path
        with Path({str(marker)!r}).open('a') as stream:
            _ = stream.write('startup\\n')
        sys.argv = ['snektest', '--version']
        runpy.run_module('snektest', run_name='__main__')
    """)
    )
    completed = subprocess.run(
        [sys.executable, "-X", "context_aware_warnings=1", str(script)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_in("snektest ", completed.stdout)
    assert_eq(marker.read_text(), "startup\n")


@test([Param(value=1, name="default"), Param(value=0, name="opt-out")], mark="slow")
def test_cli_workers_and_replacements_keep_selected_warning_policy(
    expected: int,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    pid_file = directory / f"original-worker-{expected}.txt"
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio, os, sys, warnings
        from collections.abc import Generator
        from pathlib import Path
        from snektest import assert_eq, assert_ne, fixture, load_fixture, test

        assert_eq(sys.flags.context_aware_warnings, {expected})

        @fixture(scope='run')
        def run_policy() -> Generator[int]:
            yield sys.flags.context_aware_warnings

        @fixture(scope='session')
        def session_policy() -> Generator[int]:
            yield sys.flags.context_aware_warnings

        async def verify_policy() -> None:
            assert_eq(sys.flags.context_aware_warnings, {expected})
            assert_eq(load_fixture(run_policy()), {expected})
            assert_eq(load_fixture(session_policy()), {expected})
            if {expected} == 0:
                return
            async def emit(label: str, ready: asyncio.Event, other: asyncio.Event) -> list[str]:
                with warnings.catch_warnings(record=True) as observed:
                    warnings.simplefilter('always')
                    ready.set()
                    await other.wait()
                    warnings.warn(label, UserWarning, stacklevel=1)
                    await asyncio.sleep(0)
                    return [str(item.message) for item in observed]
            left_ready, right_ready = asyncio.Event(), asyncio.Event()
            left, right = await asyncio.gather(
                emit('left', left_ready, right_ready),
                emit('right', right_ready, left_ready),
            )
            assert_eq(left, ['left'])
            assert_eq(right, ['right'])

        @test(mark='slow')
        async def test_original() -> None:
            await verify_policy()
            _ = Path({str(pid_file)!r}).write_text(str(os.getpid()))

        @test(mark='slow')
        def test_crash() -> None:
            os._exit(17)

        @test(mark='slow')
        async def test_replacement() -> None:
            assert_ne(os.getpid(), int(Path({str(pid_file)!r}).read_text()))
            await verify_policy()
        """),
        name=f"test_worker_startup_{expected}",
    )
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    if expected == 0:
        environment["PYTHON_CONTEXT_AWARE_WARNINGS"] = "0"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--json-output",
            "--workers",
            "1",
            str(path),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(
        [case["status"] for case in document["tests"]], ["passed", "error", "passed"]
    )
    assert_eq(document["run_teardown_failed"], 0)
    assert_eq(document["session_teardown_failed"], 0)
    assert_in("exited unexpectedly", document["tests"][1]["exception"]["message"])
    assert_eq(completed.stderr, "")


@test(mark="slow")
def test_cli_preserves_buffered_output_written_before_entry() -> None:
    directory = load_fixture(tmp_dir_fixture())
    marker = directory / "printed-before-entry"
    marker.unlink(missing_ok=True)
    source = dedent(f"""
        import runpy, sys
        from pathlib import Path
        marker = Path({str(marker)!r})
        if not marker.exists():
            _ = marker.write_text('printed')
            print('before CLI: ', end='')
        sys.argv = ['snektest', '--version']
        runpy.run_module('snektest', run_name='__main__')
    """)
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stderr)
    assert_in("before CLI: snektest ", completed.stdout)


@test(
    [
        Param(value=("body", 1), name="assertion-failure"),
        Param(value=("collection", 2), name="collection-error"),
        Param(value=("interrupt", 2), name="interrupted"),
    ],
    mark="slow",
)
def test_cli_preserves_unsuccessful_exit_codes_and_one_json_document(
    outcome: tuple[str, int],
) -> None:
    actor, expected = outcome
    directory = load_fixture(tmp_dir_fixture())
    source = (
        "import missing_startup_test_dependency\n"
        if actor == "collection"
        else dedent(f"""
            import signal
            from snektest import assert_eq, test

            @test(mark='fast')
            def test_outcome() -> None:
                {"signal.raise_signal(signal.SIGINT)" if actor == "interrupt" else "assert_eq(1, 2)"}
        """)
    )
    path = create_test_file(directory, source, name=f"test_startup_{actor}")
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", "--json-output", str(path)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, expected, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(document["exit_code"], expected)
    assert_eq(completed.stdout.count("\n"), 1)
    if actor == "interrupt":
        assert_eq(document["error"]["category"], "interrupted")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_posix_cli_keeps_pid_and_handles_external_interrupt_after_startup(
    workers: tuple[str, ...],
) -> None:
    if os.name == "nt":
        skip(
            "External POSIX signal delivery; portable SIGINT exit coverage is separate"
        )
    directory = load_fixture(tmp_dir_fixture())
    ready, cleaned = directory / "signal-ready", directory / "signal-cleaned"
    ready.unlink(missing_ok=True)
    cleaned.unlink(missing_ok=True)
    path = create_test_file(
        directory,
        dedent(f"""
        import asyncio, os
        from collections.abc import Generator
        from pathlib import Path
        from snektest import fixture, load_fixture, test

        @fixture
        def resource() -> Generator[None]:
            yield None
            _ = Path({str(cleaned)!r}).write_text('cleaned')

        @test(mark='slow')
        async def test_waits() -> None:
            _ = load_fixture(resource())
            _ = Path({str(ready)!r}).write_text(str(os.getpid()))
            await asyncio.Event().wait()
        """),
        name="test_startup_signal",
    )
    source = dedent(f"""
        import runpy, signal, sys
        signal.signal(signal.SIGINT, signal.default_int_handler)
        sys.argv = ['snektest', '--json-output', *{workers!r}, {str(path)!r}]
        runpy.run_module('snektest', run_name='__main__')
    """)
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    with subprocess.Popen(
        [sys.executable, "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert_eq(ready.exists(), True, msg="CLI did not reach the waiting body")
            if not workers:
                assert_eq(int(ready.read_text()), process.pid)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                _ = process.communicate(timeout=5)
    assert_eq(process.returncode, 2, msg=stdout + stderr)
    assert_eq(json.loads(stdout)["error"]["category"], "interrupted")
    if not workers:
        assert_eq(cleaned.read_text(), "cleaned")


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
def test_posix_cli_preserves_inherited_ignored_signals(
    workers: tuple[str, ...],
) -> None:
    if os.name == "nt":
        skip("POSIX inherited signal dispositions")
    directory = load_fixture(tmp_dir_fixture())
    selected = directory / "selected-signal-dispositions"
    selected.unlink(missing_ok=True)
    path = create_test_file(
        directory,
        dedent("""
        import signal, subprocess, sys
        from snektest import assert_eq, test

        @test(mark='slow')
        def inherited_ignores() -> None:
            assert_eq(sys.flags.context_aware_warnings, 1)
            assert_eq(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            assert_eq(signal.getsignal(signal.SIGQUIT), signal.SIG_IGN)
            assert_eq(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            signal.raise_signal(signal.SIGINT)
            child = subprocess.run(
                [sys.executable, '-c',
                 'import signal; from snektest import assert_eq; '
                 'assert_eq(signal.getsignal(signal.SIGINT), signal.SIG_IGN); '
                 'signal.raise_signal(signal.SIGINT); print("survived")'],
                capture_output=True, text=True, check=False, timeout=5,
            )
            assert_eq(child.returncode, 0, msg=child.stderr)
            assert_eq(child.stdout.strip(), 'survived')
        """),
        name="test_startup_ignored_signals",
    )
    source = dedent(f"""
        import runpy, signal, sys
        from pathlib import Path
        marker = Path({str(selected)!r})
        if not marker.exists():
            _ = marker.write_text('selected')
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGQUIT, signal.SIG_IGN)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        sys.argv = ['snektest', '--json-output', *{workers!r}, {str(path)!r}]
        runpy.run_module('snektest', run_name='__main__')
    """)
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    [
        Param(value=False, name="shell-background"),
        Param(value=True, name="explicit-reset"),
    ],
    mark="slow",
)
def test_posix_background_shell_signal_policy(
    workers: tuple[str, ...],
    reset: bool,  # noqa: FBT001 -- parameterized policy
) -> None:
    if os.name == "nt":
        skip("POSIX noninteractive shell background jobs")
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        dedent(f"""
        import signal, subprocess, sys
        from snektest import assert_eq, test

        @test(mark='slow')
        def background_policy() -> None:
            assert_eq(callable(signal.getsignal(signal.SIGINT)), {reset!r})
            assert_eq(signal.getsignal(signal.SIGQUIT), signal.SIG_IGN)
            child = subprocess.run(
                [sys.executable, '-c',
                 'import signal; from snektest import assert_eq; '
                 'assert_eq(signal.getsignal(signal.SIGINT), '
                 'signal.default_int_handler if {reset!r} else signal.SIG_IGN)'],
                capture_output=True, text=True, check=False, timeout=5,
            )
            assert_eq(child.returncode, 0, msg=child.stderr)
        """),
        name="test_background_policy",
    )
    # Reset in a Python launcher: a shell can refuse to reset signals ignored on
    # its own entry. exec keeps the shell's $! PID as the actual CLI PID.
    launcher = (
        "import os, signal, sys; "
        "signal.signal(signal.SIGINT, signal.SIG_DFL); "
        "os.execv(sys.executable, [sys.executable, '-m', 'snektest', *sys.argv[1:]])"
    )
    command = (
        [sys.executable, "-c", launcher]
        if reset
        else [sys.executable, "-m", "snektest"]
    )
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    completed = subprocess.run(
        [
            "/bin/sh",
            "-c",
            '"$@" </dev/null & child=$!; wait "$child"',
            "signal-test",
            *command,
            "--json-output",
            *workers,
            str(path),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=20,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)
    assert_eq(completed.stderr, "")


@test(
    [Param(value=False, name="no-relaunch"), Param(value=True, name="relaunch")],
    mark="slow",
)
def test_posix_custom_sigint_follows_python_exec_semantics(relaunch: bool) -> None:  # noqa: FBT001 -- parameterized policy
    if os.name == "nt":
        skip("POSIX exec signal semantics")
    directory = load_fixture(tmp_dir_fixture())
    marker = directory / "custom-handler-installed"
    marker.unlink(missing_ok=True)
    path = create_test_file(
        directory,
        dedent(f"""
        import signal
        from snektest import assert_eq, test

        @test(mark='fast')
        def custom_policy() -> None:
            if not {relaunch!r}:
                signal.raise_signal(signal.SIGINT)
        """),
        name="test_custom_policy",
    )
    source = dedent(f"""
        import runpy, signal, sys
        from pathlib import Path
        marker = Path({str(marker)!r})
        if not marker.exists():
            _ = marker.write_text('installed')
            signal.signal(signal.SIGINT, lambda signum, frame: None)
        from snektest import assert_eq
        if sys.flags.context_aware_warnings:
            assert_eq(signal.getsignal(signal.SIGINT) is signal.default_int_handler, {relaunch!r})
        sys.argv = ['snektest', '--json-output', {str(path)!r}]
        runpy.run_module('snektest', run_name='__main__')
    """)
    environment = dict(os.environ)
    environment.pop("PYTHON_CONTEXT_AWARE_WARNINGS", None)
    options = [] if relaunch else ["-X", "context_aware_warnings=1"]
    completed = subprocess.run(
        [sys.executable, *options, "-c", source],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(json.loads(completed.stdout)["passed"], 1)
