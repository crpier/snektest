"""Concurrent worker awaits share publication without competing pipe readers."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from typing import Any

from snektest import Param, assert_eq, test
from testutils.helpers import run_test_subprocess


def _run_concurrent_loads(mode: str, workers: str) -> dict[str, Any]:
    with TemporaryDirectory() as temporary:
        test_file = Path(temporary) / "test_concurrent.py"
        _ = test_file.write_text(
            dedent(f"""\
            import asyncio
            from collections.abc import AsyncGenerator
            from snektest import FixtureError, assert_eq, assert_is, assert_isinstance, assert_raises, fixture, load_fixture, test

            def fail_decode() -> object:
                raise FixtureError("decode failed")

            class Descriptor:
                def __reduce__(self) -> tuple[object, tuple[()]]:
                    return fail_decode, ()

            @fixture(scope="run")
            async def descriptor() -> AsyncGenerator[list[str] | Descriptor]:
                await asyncio.sleep(0.1)
                yield Descriptor() if "decode" in {mode!r} else ["ready"]

            @fixture(scope="run")
            async def other() -> AsyncGenerator[list[str]]:
                yield ["other"]

            @test(mark="fast")
            async def test_first() -> None:
                if {mode!r} == "decode":
                    errors = await asyncio.gather(
                        load_fixture(descriptor()), load_fixture(descriptor()),
                        return_exceptions=True,
                    )
                    for error in errors:
                        _ = assert_isinstance(error, FixtureError)
                    with assert_raises(FixtureError):
                        _ = await load_fixture(descriptor())
                    return
                if {mode!r} in ("cancel-waiter", "abandon", "abandon-decode"):
                    waiter = asyncio.create_task(load_fixture(descriptor()))
                    await asyncio.sleep(0)
                    _ = waiter.cancel()
                    with assert_raises(asyncio.CancelledError):
                        _ = await waiter
                    if {mode!r}.startswith("abandon"):
                        return
                    loaded = await load_fixture(descriptor())
                    assert_eq(loaded, ["ready"])
                else:
                    descriptors = await asyncio.gather(
                        load_fixture(descriptor()),
                        load_fixture(other() if {mode!r} == "different" else descriptor()),
                    )
                    assert_eq(descriptors, [["ready"], ["other" if {mode!r} == "different" else "ready"]])
                    if {mode!r} == "same":
                        assert_is(descriptors[0], descriptors[1])
                    loaded = descriptors[0]
                assert_is(await load_fixture(descriptor()), loaded)

            @test(mark="fast")
            async def test_later() -> None:
                if "decode" in {mode!r}:
                    assert_eq(await load_fixture(other()), ["other"])
                else:
                    assert_eq(await load_fixture(descriptor()), ["ready"])
        """)
        )
        arguments = ("--workers", workers) if workers else ()
        return run_test_subprocess(test_file, *arguments, timeout=30)


@test(
    [
        Param((mode, workers), f"{mode}-{workers or 'local'}")
        for mode, workers in (
            ("same", ""),
            ("different", ""),
            ("same", "1"),
            ("same", "2"),
            ("different", "1"),
            ("different", "2"),
            ("cancel-waiter", "1"),
            ("cancel-waiter", "2"),
            ("abandon", "1"),
            ("abandon", "2"),
            ("decode", "1"),
            ("decode", "2"),
            ("abandon-decode", "1"),
            ("abandon-decode", "2"),
        )
    ],
    mark="slow",
)
async def test_concurrent_run_loads(case: tuple[str, str]) -> None:
    """Loads, cancelled waiters and cached reloads leave the protocol synchronized."""
    result = await asyncio.to_thread(_run_concurrent_loads, case[0], case[1])
    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 2)
    assert_eq(result["stderr"], "")
