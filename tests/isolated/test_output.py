from __future__ import annotations

import sys
import warnings
from typing import Any, cast
from unittest.mock import patch

from snektest import assert_eq, assert_in, test
from snektest.output import StdinProxy, capture_output, maybe_capture_output


@test()
def test_capture_output_emits_warnings_and_restores_sys() -> None:
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    orig_stdin = sys.stdin

    with capture_output() as (_buf, captured_warnings):
        warnings.warn("hi", stacklevel=1)

    assert_eq(sys.stdout, orig_stdout)
    assert_eq(sys.stderr, orig_stderr)
    assert_eq(sys.stdin, orig_stdin)
    assert_eq(any("hi" in w for w in captured_warnings), True)


@test()
def test_maybe_capture_output_false_branch() -> None:
    with maybe_capture_output(False) as (buf, warning_list):
        assert_eq(buf.getvalue(), "")
        assert_eq(warning_list, [])


@test()
def test_stdin_proxy_covers_proxy_methods_and_properties() -> None:
    calls: list[str] = []

    class DummyStdin:
        closed = False
        encoding = "utf-8"
        mode = "r"
        name = "dummy"

        def __init__(self) -> None:
            self._iter = iter(["line1\n"])

        def __iter__(self) -> Any:
            return self

        def __next__(self) -> str:
            return next(self._iter)

        def read(self, size: int = -1) -> str:
            _ = size
            return "data"

        def readline(self, size: int = -1) -> str:
            _ = size
            return "line\n"

        def readlines(self, hint: int = -1) -> list[str]:
            _ = hint
            return ["a\n", "b\n"]

        def fileno(self) -> int:
            return 0

        def isatty(self) -> bool:
            return False

        some_attr = "x"

    def disable() -> None:
        calls.append("disabled")

    proxy = StdinProxy(cast(Any, DummyStdin()), disable)

    assert_eq(getattr(proxy, "some_attr"), "x")
    assert_eq(proxy.read(), "data")
    assert_eq(proxy.readline(), "line\n")
    assert_eq(proxy.readlines(), ["a\n", "b\n"])

    _ = iter(proxy)
    assert_eq(next(proxy), "line1\n")

    assert_eq(proxy.fileno(), 0)
    assert_eq(proxy.isatty(), False)

    proxy.close()
    assert_eq(proxy.closed, False)
    assert_eq(proxy.encoding, "utf-8")
    assert_eq(proxy.mode, "r")
    assert_eq(proxy.name, "dummy")

    assert_eq(len(calls) >= 1, True)


@test()
def test_breakpoint_paths_custom_hook_and_inline_message() -> None:
    called: list[str] = []

    def custom_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        called.append("custom")

    with patch.object(sys, "breakpointhook", custom_hook):
        with capture_output():
            breakpoint()

    assert_eq(called, ["custom"])

    def dummy_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        called.append("args")

    with (
        patch.object(sys, "__breakpointhook__", dummy_hook),
        patch.object(sys, "breakpointhook", dummy_hook),
    ):
        with capture_output():
            breakpoint(1)

    assert_in("args", called)

    called.clear()

    with (
        patch.object(sys, "__breakpointhook__", dummy_hook),
        patch.object(sys, "breakpointhook", dummy_hook),
        patch("snektest.output.inspect.currentframe", return_value=None),
    ):
        with capture_output():
            breakpoint()

    seen: list[str] = []

    class DummyPdb:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        def message(self, msg: str) -> None:
            seen.append(msg)

        def set_trace(self, frame: Any, commands: Any = None) -> None:
            _ = (frame, commands)

    def noop_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)

    with (
        patch.object(sys, "__breakpointhook__", noop_hook),
        patch.object(sys, "breakpointhook", noop_hook),
        patch("snektest.output.pdb.Pdb", DummyPdb),
    ):
        with capture_output():
            # Exercise settrace wrapper + disable-capture idempotency.
            sys.settrace(None)
            sys.settrace(None)

            # Exercise inline-Pdb path and header printing.
            breakpoint(header="HEADER", commands=["c"])

    assert_eq(seen, ["HEADER"])

    # Exercise extra kwargs branch (must be separate capture context because the
    # breakpointhook wrapper restores sys.breakpointhook after one call).
    seen.clear()

    with (
        patch.object(sys, "__breakpointhook__", noop_hook),
        patch.object(sys, "breakpointhook", noop_hook),
        patch("snektest.output.pdb.Pdb", DummyPdb),
    ):
        with capture_output():
            breakpoint(foo="bar")
