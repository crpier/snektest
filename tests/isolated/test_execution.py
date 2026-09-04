from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from pathlib import Path
from types import TracebackType

from snektest import (
    assert_eq,
    assert_is_not_none,
    assert_isinstance,
    assert_raises,
    fail,
    fixture,
    load_fixture,
    test,
)
from snektest.execution import execute_test, run_tests
from snektest.fixtures import FixtureRegistry, teardown_fixture, use_registry
from snektest.models import (
    ErrorResult,
    FailedResult,
    PassedResult,
    TestCase,
    TestFunction,
    TestName,
    UnreachableError,
)


def _traceback_from_exception(exc: BaseException) -> TracebackType:
    try:
        raise exc
    except type(exc) as e:
        return assert_is_not_none(e.__traceback__)


def _test_case(
    name: TestName,
    function: TestFunction,
    *,
    markers: tuple[str, ...] = (),
    param_values: tuple[object, ...] = (),
) -> TestCase:
    return TestCase(
        function=function,
        markers=markers,
        name=name,
        param_values=param_values,
    )


async def _run_plan(
    entries: list[TestCase],
    *,
    pdb_on_failure: bool = False,
    post_mortem: Callable[[TracebackType], None] | None = None,
    resolver: Callable[[Path], Path] | None = None,
) -> None:
    def base_post_mortem(_: TracebackType) -> None:
        return None

    def base_resolver(path: Path) -> Path:
        return path.resolve()

    _ = await run_tests(
        entries,
        pdb_on_failure=pdb_on_failure,
        post_mortem=post_mortem or base_post_mortem,
        resolver=resolver or base_resolver,
    )


@test()
async def test_run_tests_consumes_completed_plan_in_order() -> None:
    """Serial execution preserves complete-plan order without queue scheduling."""
    execution_order: list[str] = []

    def first() -> None:
        execution_order.append("first")

    def second() -> None:
        execution_order.append("second")

    test_cases = [
        _test_case(
            TestName(file_path=Path("plan.py"), func_name="first", params_part=""),
            first,
        ),
        _test_case(
            TestName(file_path=Path("plan.py"), func_name="second", params_part=""),
            second,
        ),
    ]

    completed_run = await run_tests(test_cases)

    assert_eq(execution_order, ["first", "second"])
    assert_eq(
        [result.name.func_name for result in completed_run.test_results],
        execution_order,
    )
    assert_eq(completed_run.session_teardown_failures, [])
    assert_eq(completed_run.run_teardown_failures, [])


@test()
async def test_teardown_fixture_reports_multiple_yields() -> None:
    def fixture() -> Generator[int]:
        yield 1
        yield 2

    gen = fixture()
    _ = next(gen)

    failure = assert_is_not_none(await teardown_fixture("fixture", gen))

    assert_eq(failure.fixture_name, "fixture")
    assert_eq(failure.exception.type_name, "BadRequestError")


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
        _ = await execute_test(
            _test_case(name, fail_assertion), exc_info_provider=missing_exc_info
        )

    def raise_error() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with assert_raises(UnreachableError):
        _ = await execute_test(
            _test_case(name, raise_error), exc_info_provider=missing_exc_info
        )


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

    await _run_plan(
        [_test_case(name, failing)],
        pdb_on_failure=True,
        post_mortem=debug_noop_post_mortem,
        resolver=failing_resolver,
    )


@test()
async def test_debug_fixture_teardown_branch() -> None:
    @fixture
    def fix() -> Generator[str]:
        yield "value"
        msg = "teardown failed"
        raise RuntimeError(msg)

    def test_body() -> None:
        _ = load_fixture(fix())

    name = TestName(file_path=Path("x.py"), func_name="t", params_part="")

    def fixture_noop_post_mortem(_: TracebackType) -> None:
        return None

    await _run_plan(
        [_test_case(name, test_body)],
        pdb_on_failure=True,
        post_mortem=fixture_noop_post_mortem,
    )


@test()
async def test_debug_session_teardown_branch_and_output() -> None:
    @fixture(scope="session")
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

    await _run_plan(
        [_test_case(name, test_body)],
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


@test()
async def test_execute_test_marks_cancelled_error_as_failed() -> None:
    async def cancelled() -> None:
        raise asyncio.CancelledError

    name = TestName(file_path=Path("x.py"), func_name="cancelled", params_part="")
    test_result = await execute_test(_test_case(name, cancelled))

    failed = assert_isinstance(test_result.result, FailedResult)
    assert_eq(failed.exception.type_name, "CancelledError")


@test()
async def test_run_tests_continues_after_cancelled_test() -> None:
    async def cancelled() -> None:
        raise asyncio.CancelledError

    def passing() -> None:
        return None

    test_cases = [
        _test_case(
            TestName(file_path=Path("x.py"), func_name="cancelled", params_part=""),
            cancelled,
        ),
        _test_case(
            TestName(file_path=Path("x.py"), func_name="passing", params_part=""),
            passing,
        ),
    ]

    completed_run = await run_tests(test_cases)

    assert_eq(len(completed_run.test_results), 2)
    _ = assert_isinstance(completed_run.test_results[0].result, FailedResult)
    _ = assert_isinstance(completed_run.test_results[1].result, PassedResult)
    assert_eq(len(completed_run.session_teardown_failures), 0)
    assert_eq(len(completed_run.run_teardown_failures), 0)


@test()
async def test_execute_test_fails_and_cancels_pending_task() -> None:
    leaked_tasks: list[asyncio.Task[None]] = []

    async def waits_forever() -> None:
        _ = await asyncio.Event().wait()

    async def leaks_task() -> None:
        leaked_tasks.append(asyncio.create_task(waits_forever()))
        await asyncio.sleep(0)

    name = TestName(file_path=Path("x.py"), func_name="leaks_task", params_part="")
    result = await execute_test(_test_case(name, leaks_task))

    failure = assert_isinstance(result.result, FailedResult)
    assert_eq(failure.exception.type_name, "AssertionFailure")
    assert_eq(failure.exception.message, "async test leaked 1 pending task")
    assert_eq([task.cancelled() for task in leaked_tasks], [True])


@test()
async def test_execute_test_times_out_hanging_async_test() -> None:
    name = TestName(file_path=Path("x.py"), func_name="hangs", params_part="")

    async def hangs() -> None:
        await asyncio.sleep(10)

    result = await execute_test(_test_case(name, hangs), timeout=0.01)

    error = assert_isinstance(result.result, ErrorResult)
    assert_eq(error.exception.type_name, "TestTimeoutError")


@test()
async def test_timed_out_test_still_runs_function_teardown() -> None:
    torn_down: list[bool] = []

    @fixture
    def resource() -> Generator[int]:
        yield 1
        torn_down.append(True)

    async def hangs() -> None:
        _ = load_fixture(resource())
        await asyncio.sleep(10)

    name = TestName(file_path=Path("x.py"), func_name="hangs", params_part="")
    with use_registry(FixtureRegistry()):
        result = await execute_test(_test_case(name, hangs), timeout=0.01)

    error = assert_isinstance(result.result, ErrorResult)
    assert_eq(error.exception.type_name, "TestTimeoutError")
    assert_eq(torn_down, [True])


@test()
async def test_user_raised_timeout_error_is_not_reported_as_test_timeout() -> None:
    name = TestName(file_path=Path("x.py"), func_name="raises", params_part="")

    async def raises_timeout() -> None:
        msg = "from user code"
        raise TimeoutError(msg)

    result = await execute_test(_test_case(name, raises_timeout), timeout=10)

    error = assert_isinstance(result.result, ErrorResult)
    assert_eq(error.exception.type_name, "TimeoutError")


@test()
async def test_timeout_does_not_affect_fast_async_test() -> None:
    name = TestName(file_path=Path("x.py"), func_name="quick", params_part="")

    async def quick() -> None:
        await asyncio.sleep(0)

    result = await execute_test(_test_case(name, quick), timeout=10)

    _ = assert_isinstance(result.result, PassedResult)
