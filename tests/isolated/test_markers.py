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
from snektest.models import FilterItem, PassedResult, TestName, TestResult
from snektest.utils import get_test_function_markers

MarkerDecorator = Callable[[Any], Any]


def _apply_markers(func: Callable[[], object | None], mark: Any) -> None:
    marker_decorator = cast("MarkerDecorator", test(mark=mark))
    marker_decorator(func)


@test()
def test_markers_are_stored_on_test_function() -> None:
    @test()
    def test_marked() -> None:
        pass

    _apply_markers(test_marked, "fast")
    assert_eq(get_test_function_markers(test_marked), ("fast",))


@test()
def test_markers_reject_invalid_value() -> None:
    def marked() -> None:
        pass

    with assert_raises(TypeError):
        _apply_markers(marked, ["fast", 123])
    with assert_raises(TypeError):
        _apply_markers(marked, "needs-s3")
    with assert_raises(TypeError):
        _apply_markers(marked, ("fast", "slow"))
    with assert_raises(TypeError):
        _apply_markers(marked, ["fast"])


@test()
def test_marker_normalization_inputs() -> None:
    @test(mark="medium")
    def test_marked() -> None:
        pass

    assert_eq(get_test_function_markers(test_marked), ("medium",))


@test()
def test_marker_literal_values() -> None:
    @test(mark="slow")
    def test_marked() -> None:
        pass

    assert_eq(get_test_function_markers(test_marked), ("slow",))


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
        test_case = await asyncio.wait_for(queue.get(), timeout=1)
        assert_eq(test_case.name.func_name, "test_fast")
        assert_eq(test_case.markers, ("fast",))
        queue.shutdown()

        queue_empty: TestsQueue = TestsQueue()
        load_tests_from_file(
            file_path,
            filter_item,
            queue_empty,
            loop,
            mark="medium",
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
def test_parse_cli_args_mark_rejects_unknown_marker() -> None:
    result = parse_cli_args(["--mark", "needs-s3"])
    assert_eq(result, 2)


@test()
def test_json_output_includes_markers() -> None:
    test_result = TestResult(
        name=TestName(
            file_path=Path("tests/test_fake.py"), func_name="test_x", params_part=""
        ),
        duration=0.5,
        result=PassedResult(),
        markers=("fast",),
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
    assert_eq(parsed["tests"][0]["markers"], ["fast"])
    assert_eq(parsed["tests"][0]["status"], "passed")
