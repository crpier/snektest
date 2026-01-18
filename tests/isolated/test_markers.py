from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from snektest import assert_eq, assert_raises, test
from snektest.annotations import PyFilePath
from snektest.cli import TestRunSummary, build_json_summary, parse_cli_args
from snektest.collection import TestsQueue, load_tests_from_file
from snektest.decorators import Marker
from snektest.models import FilterItem, PassedResult, TestName, TestResult
from snektest.utils import get_test_function_markers

MarkerDecorator = Callable[[Any], Any]


def _apply_markers(func: Callable[[], object | None], mark: object) -> None:
    marker_decorator = cast("MarkerDecorator", test(mark=mark))  # pyright: ignore[reportCallIssue]
    marker_decorator(func)


@test()
def test_markers_are_stored_on_test_function() -> None:
    @test()
    def test_marked() -> None:
        pass

    _apply_markers(test_marked, ("needs-s3", "fast"))
    assert_eq(get_test_function_markers(test_marked), ("needs-s3", "fast"))


@test()
def test_markers_reject_invalid_value() -> None:
    with assert_raises(TypeError):
        test(mark=["fast", 123])  # pyright: ignore[reportCallIssue]
    assert_eq(1, 1)
    assert_eq(1, 1)


@test()
def test_marker_normalization_inputs() -> None:
    assert_eq(Marker.FAST.value, "fast")
    assert_eq("custom", "custom")
    assert_eq(Marker.MEDIUM.value, "medium")
    assert_eq(Marker.SLOW.value, "slow")


@test()
def test_marker_enum_is_str() -> None:
    assert_eq(str(Marker.FAST.value), "fast")
    assert_eq(str(Marker.MEDIUM.value), "medium")


@test()
async def test_load_tests_from_file_filters_on_marker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        test_file = tmp_dir / "test_collection_markers.py"
        _ = test_file.write_text(
            """
from snektest import test

@test(mark="fast")
def test_fast() -> None:
    pass

@test()
def test_unmarked() -> None:
    pass
""".lstrip()
        )

        file_path = cast(
            "PyFilePath", TypeAdapter(PyFilePath).validate_python(test_file)
        )
        filter_item = FilterItem(str(test_file))
        loop = asyncio.get_running_loop()

        queue: TestsQueue = TestsQueue()
        load_tests_from_file(
            file_path,
            filter_item,
            queue,
            loop,
            mark="fast",
        )
        name, _func = await asyncio.wait_for(queue.get(), timeout=1)
        assert_eq(name.func_name, "test_fast")
        queue.shutdown()

        queue_empty: TestsQueue = TestsQueue()
        load_tests_from_file(
            file_path,
            filter_item,
            queue_empty,
            loop,
            mark="missing",
        )
        queue_empty.shutdown()
        with assert_raises(asyncio.QueueShutDown):
            _ = await queue_empty.get()


@test()
def test_parse_cli_args_mark_option() -> None:
    parsed = parse_cli_args(["--mark", "fast", "."])
    assert not isinstance(parsed, int)

    _potential_filter, options = parsed
    assert_eq(options.mark, "fast")


@test()
def test_parse_cli_args_mark_missing_value() -> None:
    result = parse_cli_args(["--mark"])
    assert_eq(result, 2)


@test()
def test_parse_cli_args_mark_only_once() -> None:
    result = parse_cli_args(["--mark", "fast", "--mark", "slow"])
    assert_eq(result, 2)


@test()
def test_parse_cli_args_mark_rejects_option_value() -> None:
    result = parse_cli_args(["--mark", "--pdb"])
    assert_eq(result, 2)


@test()
def test_json_output_includes_markers() -> None:
    test_result = TestResult(
        name=TestName(
            file_path=Path("tests/test_fake.py"), func_name="test_x", params_part=""
        ),
        duration=0.5,
        result=PassedResult(),
        markers=("needs-s3",),
        captured_output=StringIO(""),
        fixture_teardown_failures=[],
        fixture_teardown_output=None,
        warnings=[],
    )
    summary = TestRunSummary(
        total_tests=1,
        passed=1,
        failed=0,
        errors=0,
        fixture_teardown_failed=0,
        session_teardown_failed=0,
        test_results=[test_result],
        session_teardown_failures=[],
    )
    output = json.dumps(build_json_summary(summary))
    parsed = json.loads(output)
    assert_eq(parsed["tests"][0]["markers"], ["needs-s3"])
    assert_eq(parsed["tests"][0]["status"], "passed")
