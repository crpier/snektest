"""Tests for immutable, process-neutral execution diagnostics."""

from __future__ import annotations

import asyncio
from gc import collect
from pathlib import Path
from pickle import dumps, loads
from types import TracebackType
from typing import override
from weakref import ReferenceType, ref

from rich.console import Console

from snektest import (
    assert_eq,
    assert_is_none,
    assert_is_not_none,
    assert_isinstance,
    test,
)
from snektest.diagnostics import (
    LiveDiagnosticStore,
    snapshot_assertion_failure,
    snapshot_exception,
    use_live_diagnostic_store,
)
from snektest.execution import execute_test, run_tests
from snektest.fixtures import FixtureRegistry, use_registry
from snektest.models import (
    AssertionDiagnostic,
    AssertionFailure,
    ErrorResult,
    ExceptionDiagnostic,
    TestCase,
    TestName,
    TestResult,
)
from snektest.presenter.diff import render_assertion_failure


def _traceback_from_exception(exception: BaseException) -> TracebackType:
    try:
        raise exception
    except type(exception) as caught:
        return assert_is_not_none(caught.__traceback__)


def _snapshot(exception: BaseException) -> ExceptionDiagnostic:
    return snapshot_exception(
        type(exception), exception, _traceback_from_exception(exception)
    )


@test()
def test_exception_diagnostic_round_trips_through_stdlib_pickle() -> None:
    """Diagnostics retain messages and source without live exception state."""
    diagnostic = _snapshot(RuntimeError("boom"))

    restored = assert_isinstance(
        loads(dumps(diagnostic)),  # noqa: S301
        ExceptionDiagnostic,
    )

    assert_eq(restored, diagnostic)
    assert_eq(restored.type_name, "RuntimeError")
    assert_eq(restored.message, "boom")
    assert_eq(restored.frames[-1].function_name, "_traceback_from_exception")
    assert_eq(restored.frames[-1].source_line, "        raise exception")


@test()
def test_assertion_diagnostic_round_trips_and_preserves_rendering() -> None:
    """Collection diffs remain width-sensitive after values are snapshotted."""

    class DisplayValue:
        def __init__(self, label: str) -> None:
            self.label = label

        @override
        def __repr__(self) -> str:
            return f"DisplayValue({self.label!r})"

    failure = AssertionFailure(
        "lists differ",
        actual=[DisplayValue("actual")],
        expected=[DisplayValue("expected")],
    )
    diagnostic = snapshot_assertion_failure(failure)
    live_console = Console(record=True, width=40)
    snapshot_console = Console(record=True, width=40)

    render_assertion_failure(live_console, failure)
    restored = assert_isinstance(
        loads(dumps(diagnostic)),  # noqa: S301
        AssertionDiagnostic,
    )
    render_assertion_failure(snapshot_console, restored)

    assert_eq(snapshot_console.export_text(), live_console.export_text())


@test()
def test_exception_group_snapshot_has_bounded_breadth() -> None:
    grouped = ExceptionGroup(
        "many",
        [ValueError(str(index)) for index in range(150)],
    )

    diagnostic = _snapshot(grouped)

    assert_eq(len(diagnostic.exceptions), 100)


@test()
async def test_normal_run_releases_failed_test_locals_before_the_next_case() -> None:
    """Immutable diagnostics do not keep prior failure frames alive without PDB."""

    class Payload: ...

    references: list[ReferenceType[Payload]] = []
    released_before_next_case: list[bool] = []

    def failing_test() -> None:
        payload = Payload()
        references.append(ref(payload))
        raise RuntimeError

    def observe_previous_failure() -> None:
        _ = collect()
        released_before_next_case.append(references[0]() is None)

    test_cases = [
        TestCase(
            function=failing_test,
            markers=(),
            name=TestName(
                file_path=Path(__file__),
                func_name="failing_test",
                params_part="",
            ),
        ),
        TestCase(
            function=observe_previous_failure,
            markers=(),
            name=TestName(
                file_path=Path(__file__),
                func_name="observe_previous_failure",
                params_part="",
            ),
        ),
    ]

    _ = await run_tests(test_cases)

    assert_eq(released_before_next_case, [True])


@test()
async def test_public_error_result_does_not_retain_frame_locals() -> None:
    """Keeping a public result alive does not keep failed test locals alive."""

    class Payload: ...

    async def execute_failure() -> tuple[TestResult, ReferenceType[Payload]]:
        references: list[ReferenceType[Payload]] = []

        def failing_test() -> None:
            payload = Payload()
            references.append(ref(payload))
            raise RuntimeError

        with (
            use_live_diagnostic_store(LiveDiagnosticStore()),
            use_registry(FixtureRegistry()),
        ):
            result = await execute_test(
                TestCase(
                    function=failing_test,
                    markers=(),
                    name=TestName(
                        file_path=Path(__file__),
                        func_name="failing_test",
                        params_part="",
                    ),
                    param_values=(),
                )
            )
        return result, references[0]

    execution_task = asyncio.create_task(execute_failure())
    result, payload_reference = await execution_task
    del execution_task

    await asyncio.sleep(0)
    _ = collect()
    restored = assert_isinstance(
        loads(dumps(result)),  # noqa: S301
        TestResult,
    )
    error = assert_isinstance(restored.result, ErrorResult)

    assert_is_none(payload_reference())
    assert_eq(error.exception.type_name, "RuntimeError")
    assert_eq(restored.captured_output, "")
    assert_eq(restored.fixture_teardown_failures, ())
    assert_eq(restored.warnings, ())
