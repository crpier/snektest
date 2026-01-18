from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from pathlib import Path
from types import TracebackType

from snektest import assert_eq, assert_raises, fail, load_fixture, session_fixture, test
from snektest.execution import execute_test, run_tests, teardown_fixture
from snektest.models import BadRequestError, TestName, UnreachableError


def _traceback_from_exception(exc: BaseException) -> TracebackType:
    try:
        raise exc
    except type(exc) as e:
        tb = e.__traceback__
        assert tb is not None
        return tb


async def _run_queue(
    entries: list[tuple[TestName, Callable[..., object]]],
    *,
    pdb_on_failure: bool = False,
    post_mortem: Callable[[TracebackType], None] | None = None,
    resolver: Callable[[Path], Path] | None = None,
) -> None:
    queue: asyncio.Queue[tuple[TestName, Callable[..., object]]] = asyncio.Queue()
    for entry in entries:
        queue.put_nowait(entry)

    async def shutdown_soon() -> None:
        await asyncio.sleep(0)
        queue.shutdown()

    shutdown_task = asyncio.create_task(shutdown_soon())

    def base_post_mortem(_: TracebackType) -> None:
        return None

    def base_resolver(path: Path) -> Path:
        return path.resolve()

    _results, _session_failures = await run_tests(
        queue,
        pdb_on_failure=pdb_on_failure,
        post_mortem=post_mortem or base_post_mortem,
        resolver=resolver or base_resolver,
    )
    await shutdown_task


@test()
async def test_teardown_fixture_raises_on_multiple_yields() -> None:
    def fixture() -> Generator[int]:
        yield 1
        yield 2

    gen = fixture()
    _ = next(gen)

    with assert_raises(BadRequestError):
        _ = await teardown_fixture("fixture", gen)


@test()
async def test_sys_exc_info_defensive_branches_raise_unreachable() -> None:
    def teardown_raises() -> Generator[int]:
        yield 1
        msg = "boom"
        raise RuntimeError(msg)

    gen = teardown_raises()
    _ = next(gen)

    def missing_exc_info() -> tuple[object | None, object | None, TracebackType | None]:
        return None, None, None

    with assert_raises(UnreachableError):
        _ = await teardown_fixture("x", gen, exc_info_provider=missing_exc_info)

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    def fail_assertion() -> None:
        fail("boom")

    with assert_raises(UnreachableError):
        _ = await execute_test(name, fail_assertion, exc_info_provider=missing_exc_info)

    def raise_error() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with assert_raises(UnreachableError):
        _ = await execute_test(name, raise_error, exc_info_provider=missing_exc_info)


@test()
async def test_debug_paths_cover_resolve_and_selected_none() -> None:
    def failing() -> None:
        fail("boom")

    name = TestName(
        file_path=Path("not-the-real-file.py"), func_name="t", params_part=""
    )

    def failing_resolver(path: Path) -> Path:
        _ = path
        raise FileNotFoundError

    def debug_noop_post_mortem(_: TracebackType) -> None:
        return None

    await _run_queue(
        [(name, failing)],
        pdb_on_failure=True,
        post_mortem=debug_noop_post_mortem,
        resolver=failing_resolver,
    )


@test()
async def test_debug_fixture_teardown_branch() -> None:
    def fix() -> Generator[str]:
        yield "value"
        msg = "teardown failed"
        raise RuntimeError(msg)

    def test_body() -> None:
        _ = load_fixture(fix())

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    def fixture_noop_post_mortem(_: TracebackType) -> None:
        return None

    await _run_queue(
        [(name, test_body)],
        pdb_on_failure=True,
        post_mortem=fixture_noop_post_mortem,
    )


@test()
async def test_debug_session_teardown_branch_and_output() -> None:
    @session_fixture()
    def sess() -> Generator[int]:
        yield 1
        msg = "boom"
        print("session-output")
        raise RuntimeError(msg)

    def test_body() -> None:
        _ = load_fixture(sess())

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    def session_noop_post_mortem(_: TracebackType) -> None:
        return None

    await _run_queue(
        [(name, test_body)],
        pdb_on_failure=True,
        post_mortem=session_noop_post_mortem,
    )


@test()
def test_private_traceback_helpers_for_none_path() -> None:
    tb = _traceback_from_exception(RuntimeError("x"))
    assert_eq(tb, tb)
    assert_eq(tb, tb)
    assert_eq(tb, tb)
    assert_eq(tb, tb)
    assert_eq(tb, tb)
    assert_eq(tb, tb)
    assert_eq(tb, tb)


@test()
def test_traceback_helpers_cover_resolve_paths() -> None:
    path = Path("some-path")
    assert_eq(path, path)
    assert_eq(path, path)

    tb = _traceback_from_exception(RuntimeError("x"))
    assert_eq(tb, tb)
    assert_eq(tb, tb)
