from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

from pydantic import TypeAdapter

from snektest import assert_eq, assert_raises, test
from snektest.annotations import PyFilePath
from snektest.collection import TestsQueue, load_tests_from_file
from snektest.models import CollectionError, FilterItem


@test()
async def test_load_tests_from_file_caches_module() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        test_file = tmp_dir / "test_collection_generated.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )

        file_path = cast(PyFilePath, TypeAdapter(PyFilePath).validate_python(test_file))
        filter_item = FilterItem(str(test_file))
        loop = asyncio.get_running_loop()

        queue: TestsQueue = TestsQueue()
        load_tests_from_file(file_path, filter_item, queue, loop)
        _ = await asyncio.wait_for(queue.get(), timeout=1)

        queue2: TestsQueue = TestsQueue()
        load_tests_from_file(file_path, filter_item, queue2, loop)
        _ = await asyncio.wait_for(queue2.get(), timeout=1)


@test()
async def test_load_tests_from_file_filters_function_and_params() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        test_file = tmp_dir / "test_collection_generated_params.py"
        _ = test_file.write_text(
            """
from snektest import test
from snektest.models import Param

@test([Param(1, 'one')])
def test_param(x: int) -> None:
    _ = x

@test()
def test_other() -> None:
    pass
""".lstrip()
        )

        file_path = cast(PyFilePath, TypeAdapter(PyFilePath).validate_python(test_file))
        loop = asyncio.get_running_loop()

        queue: TestsQueue = TestsQueue()
        load_tests_from_file(
            file_path,
            FilterItem(f"{test_file}::test_other"),
            queue,
            loop,
        )
        name, _func = await asyncio.wait_for(queue.get(), timeout=1)
        assert_eq(name.func_name, "test_other")

        queue2: TestsQueue = TestsQueue()
        load_tests_from_file(
            file_path,
            FilterItem(f"{test_file}::test_param[does not match]"),
            queue2,
            loop,
        )
        queue2.shutdown()
        with assert_raises(asyncio.QueueShutDown):
            _ = await queue2.get()


@test()
def test_load_tests_from_file_spec_loader_failure_raises_collection_error() -> None:
    from snektest import collection

    def fake_spec(*args: object, **kwargs: object) -> None:
        return None

    with (
        patch.object(collection, "spec_from_file_location", fake_spec),
        assert_raises(CollectionError),
    ):
        queue: TestsQueue = TestsQueue()
        loop = asyncio.new_event_loop()
        try:
            load_tests_from_file(
                cast(
                    PyFilePath, TypeAdapter(PyFilePath).validate_python(Path(__file__))
                ),
                FilterItem(str(Path(__file__))),
                queue,
                loop,
            )
        finally:
            loop.close()
