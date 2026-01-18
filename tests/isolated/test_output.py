from __future__ import annotations

import sys
import warnings
from collections.abc import Callable
from typing import Any, cast

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

    proxy = StdinProxy(cast("Any", DummyStdin()), disable)

    assert_eq(proxy.some_attr, "x")
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


def _set_breakpoint_hooks(hook: Callable[..., Any]) -> Callable[[], None]:
    original_hook = sys.__breakpointhook__
    sys.__breakpointhook__ = hook
    sys.breakpointhook = hook

    def restore() -> None:
        sys.__breakpointhook__ = original_hook
        sys.breakpointhook = original_hook

    return restore


@test()
def test_breakpoint_custom_hook() -> None:
    called: list[str] = []

    def custom_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        called.append("custom")

    sys.breakpointhook = custom_hook
    try:
        with capture_output():
            breakpoint()
    finally:
        sys.breakpointhook = sys.__breakpointhook__

    assert_eq(called, ["custom"])


@test()
def test_breakpoint_args_path() -> None:
    called: list[str] = []

    def dummy_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        called.append("args")

    restore = _set_breakpoint_hooks(dummy_hook)
    try:
        with capture_output():
            breakpoint(1)
    finally:
        restore()

    assert_in("args", called)


@test()
def test_breakpoint_missing_frame_path() -> None:
    def dummy_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)

    def frame_provider() -> Any:
        return None

    restore = _set_breakpoint_hooks(dummy_hook)
    try:
        with capture_output(frame_provider=frame_provider):
            breakpoint()
    finally:
        restore()


@test()
def test_breakpoint_inline_pdb_paths() -> None:
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

    restore = _set_breakpoint_hooks(noop_hook)
    try:
        with capture_output(pdb_factory=DummyPdb):
            sys.settrace(None)
            sys.settrace(None)
            breakpoint(header="HEADER", commands=["c"])
    finally:
        restore()

    assert_eq(seen, ["HEADER"])


@test()
def test_breakpoint_extra_kwargs_path() -> None:
    class DummyPdb:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        def message(self, msg: str) -> None:
            _ = msg

        def set_trace(self, frame: Any, commands: Any = None) -> None:
            _ = (frame, commands)

    def noop_hook(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)

    restore = _set_breakpoint_hooks(noop_hook)
    try:
        with capture_output(pdb_factory=DummyPdb):
            breakpoint(foo="bar")
    finally:
        restore()
