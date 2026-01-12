from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from pathlib import Path
from types import TracebackType
from unittest.mock import patch

from snektest import assert_eq, assert_raises, fail, load_fixture, session_fixture, test
from snektest.execution import execute_test, run_tests, teardown_fixture
from snektest.models import BadRequestError, TestName, UnreachableError


def _traceback_from_exception(exc: BaseException) -> TracebackType:
    try:
        raise exc
    except type(exc) as e:  # noqa: BLE001
        tb = e.__traceback__
        assert tb is not None
        return tb


async def _run_queue(
    entries: list[tuple[TestName, Callable[..., object]]],
    *,
    pdb_on_failure: bool = False,
) -> None:
    queue: asyncio.Queue[tuple[TestName, Callable[..., object]]] = asyncio.Queue()
    for entry in entries:
        queue.put_nowait(entry)

    async def shutdown_soon() -> None:
        await asyncio.sleep(0)
        queue.shutdown()

    _ = asyncio.create_task(shutdown_soon())
    _results, _session_failures = await run_tests(queue, pdb_on_failure=pdb_on_failure)


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
        raise RuntimeError("boom")

    gen = teardown_raises()
    _ = next(gen)

    with patch("snektest.execution.sys.exc_info", return_value=(None, None, None)):
        with assert_raises(UnreachableError):
            _ = await teardown_fixture("x", gen)

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    def fail_assertion() -> None:
        fail("boom")

    with patch("snektest.execution.sys.exc_info", return_value=(None, None, None)):
        with assert_raises(UnreachableError):
            _ = await execute_test(name, fail_assertion)

    def raise_error() -> None:
        raise RuntimeError("boom")

    with patch("snektest.execution.sys.exc_info", return_value=(None, None, None)):
        with assert_raises(UnreachableError):
            _ = await execute_test(name, raise_error)


@test()
async def test_debug_paths_cover_resolve_and_selected_none() -> None:
    def failing() -> None:
        fail("boom")

    name = TestName(
        file_path=Path("not-the-real-file.py"), func_name="t", params_part=""
    )

    with (
        patch("snektest.execution.pdb.post_mortem"),
        patch("pathlib.Path.resolve", side_effect=FileNotFoundError),
    ):
        await _run_queue([(name, failing)], pdb_on_failure=True)


@test()
async def test_debug_fixture_teardown_branch() -> None:
    def fix() -> Generator[str]:
        yield "value"
        raise RuntimeError("teardown failed")

    def test_body() -> None:
        _ = load_fixture(fix())

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    with patch("snektest.execution.pdb.post_mortem"):
        await _run_queue([(name, test_body)], pdb_on_failure=True)


@test()
async def test_debug_session_teardown_branch_and_output() -> None:
    @session_fixture()
    def sess() -> Generator[int]:
        yield 1
        print("session-output")
        raise RuntimeError("boom")

    def test_body() -> None:
        _ = load_fixture(sess())

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    with patch("snektest.execution.pdb.post_mortem"):
        await _run_queue([(name, test_body)], pdb_on_failure=True)


@test()
def test_private_traceback_helpers_for_none_path() -> None:
    import snektest.execution as execution

    tb = _traceback_from_exception(RuntimeError("x"))
    fn = getattr(execution, "_traceback_for_file")
    _ = fn(tb, preferred_file=None)

    # `fn` should keep the original traceback.
    assert_eq(fn(tb, preferred_file=None), tb)
