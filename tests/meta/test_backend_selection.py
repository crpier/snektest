"""Reject backend mistakes before imports and respect explicit embedding choices."""

lazy import json
lazy import subprocess
lazy import sys
lazy from pathlib import Path
lazy from textwrap import dedent
lazy from typing import Literal

lazy from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    temporary_directory,
    test,
)
lazy from testutils.helpers import create_test_file

_FLAGS: list[Param[tuple[str, ...]]] = [
    Param(value=("--backend",), name="missing"),
    Param(value=("--backend", "bogus"), name="unknown"),
    Param(value=("--backend", "Trio"), name="case-sensitive"),
    Param(value=("--backend", "trio", "--backend", "asyncio"), name="repeated"),
]
_CONFIGS: list[Param[str]] = [
    Param(value='"bogus"', name="unknown"),
    Param(value="true", name="boolean"),
    Param(value="3", name="number"),
    Param(value='["trio"]', name="list"),
]


def _selected(directory: Path) -> tuple[Path, Path]:
    imported = directory / "imported"
    path = create_test_file(
        directory,
        f"from pathlib import Path\n_ = Path({str(imported)!r}).write_text('imported')\n",
    )
    return path, imported


@test(_FLAGS, mark="slow")
def test_invalid_backend_flags_do_not_import_tests(flags: tuple[str, ...]) -> None:
    directory = temporary_directory()
    path, imported = _selected(directory)
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", "--json-output", str(path), *flags],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(json.loads(completed.stdout)["error"]["category"], "usage")
    assert_false(imported.exists())


@test(_CONFIGS, mark="slow")
def test_invalid_configured_backend_does_not_import_tests(value: str) -> None:
    directory = temporary_directory()
    path, imported = _selected(directory)
    _ = (directory / "pyproject.toml").write_text(
        f"[tool.snektest]\nbackend = {value}\n"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", "--json-output", str(path)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_in("tool.snektest.backend", json.loads(completed.stdout)["error"]["message"])
    assert_false(imported.exists())


@test(mark="slow")
def test_embedding_requires_explicit_matching_backend_and_preserves_host_work() -> None:
    directory = temporary_directory()
    imported = directory / "imported"
    path = create_test_file(
        directory,
        dedent(f"""
        from pathlib import Path
        import trio
        from snektest import test
        _ = Path({str(imported)!r}).write_text("imported")
        @test(mark="medium")
        async def test_native() -> None:
            await trio.sleep(0)
    """),
    )
    script = dedent(f"""
        import asyncio, trio
        from pathlib import Path
        from snektest import BadRequestError, assert_eq, assert_false, assert_raises, assert_true
        from snektest.cli import run_tests_programmatic
        from snektest.models import FilterItem
        filters = [FilterItem({str(path)!r})]
        async def wrong_asyncio():
            with assert_raises(BadRequestError, match="matching running trio"):
                _ = await run_tests_programmatic(filters, backend="trio")
        asyncio.run(wrong_asyncio())
        async def main():
            with assert_raises(BadRequestError, match="matching running asyncio"):
                _ = await run_tests_programmatic(filters)
            assert_false(Path({str(imported)!r}).exists())
            ready, finished = trio.Event(), trio.Event()
            async def host_work():
                try:
                    ready.set()
                    await trio.sleep_forever()
                finally:
                    finished.set()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(host_work)
                await ready.wait()
                result = await run_tests_programmatic(filters, backend="trio")
                assert_eq(result.passed, 1)
                assert_false(finished.is_set())
                nursery.cancel_scope.cancel()
            assert_true(finished.is_set())
        trio.run(main)
    """)
    completed = subprocess.run(
        [sys.executable, "-X", "context_aware_warnings=1", "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stdout, "")
    assert_eq(completed.stderr, "")


@test(
    [
        Param[Literal["asyncio", "trio"]](value="asyncio", name="asyncio"),
        Param[Literal["asyncio", "trio"]](value="trio", name="trio"),
    ],
    mark="slow",
)
def test_property_handoffs_copy_each_examples_strategy_context(
    backend: Literal["asyncio", "trio"],
) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        import anyio
        from contextvars import ContextVar
        from hypothesis import settings, strategies as st
        from snektest import assert_eq, test_hypothesis
        observed: ContextVar[int] = ContextVar("strategy-context", default=-1)
        def record(value: int) -> int:
            _ = observed.set(value)
            return value
        @settings(max_examples=4, deadline=None)
        @test_hypothesis(st.integers(min_value=0, max_value=10).map(record), mark="medium")
        async def test_dynamic_context(value: int) -> None:
            assert_eq(observed.get(), value)
            await anyio.sleep(0)
            assert_eq(observed.get(), value)
    """),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--backend",
            backend,
            "--json-output",
            str(path),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    assert_eq(json.loads(completed.stdout)["passed"], 1)
