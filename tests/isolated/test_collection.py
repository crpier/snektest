"""Tests for test-module discovery and collection."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from snektest import assert_eq, assert_in, assert_raises, test
from snektest.annotations import PyFilePath
from snektest.collection import (
    TestsQueue,
    collect_tests_from_filters,
    generate_file_list,
    load_tests_from_file,
)
from snektest.models import CollectionError, FilterItem


@test(mark="slow")
def test_generate_file_list_excludes_gitignored_files() -> None:
    """Recursive discovery leaves ignored generated tests out of a run."""
    with tempfile.TemporaryDirectory() as tmp:
        repository = Path(tmp)
        _ = subprocess.run(
            ["git", "init", "--quiet", str(repository)],
            check=True,
            capture_output=True,
        )
        _ = (repository / ".gitignore").write_text("ignored/\n")
        _ = (repository / "test_included.py").write_text("")
        ignored_directory = repository / "ignored"
        ignored_directory.mkdir()
        _ = (ignored_directory / "test_generated.py").write_text("")

        file_paths = generate_file_list(FilterItem(str(repository)))

    assert_eq([path.name for path in file_paths], ["test_included.py"])


@test()
def test_generate_file_list_sorts_directory_candidates() -> None:
    """Filesystem enumeration order does not affect the canonical plan."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        _ = (directory / "test_z.py").write_text("")
        _ = (directory / "test_a.py").write_text("")

        file_paths = generate_file_list(FilterItem(str(directory)))

    assert_eq([path.name for path in file_paths], ["test_a.py", "test_z.py"])


@test()
def test_collection_uses_definition_order_and_assigns_ordinals() -> None:
    """Cases retain source order rather than alphabetical member order."""
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_definition_order.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_z() -> None:
    pass

@test()
def test_a() -> None:
    pass
""".lstrip()
        )

        test_cases = collect_tests_from_filters([FilterItem(str(test_file))])

    assert_eq([case.name.func_name for case in test_cases], ["test_z", "test_a"])
    assert_eq([case.ordinal for case in test_cases], [0, 1])


@test()
def test_collection_preserves_repeated_filter_occurrences() -> None:
    """Selecting the same case twice produces two distinct plan ordinals."""
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_repeated_filter.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def test_one() -> None:
    pass
""".lstrip()
        )
        selected_filter = FilterItem(str(test_file))

        test_cases = collect_tests_from_filters([selected_filter, selected_filter])

    assert_eq([str(case.name) for case in test_cases], [str(test_cases[0].name)] * 2)
    assert_eq([case.ordinal for case in test_cases], [0, 1])


@test()
def test_collection_rejects_duplicate_case_identity_within_selection() -> None:
    """Two discovered callables cannot silently claim one case identity."""
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_duplicate_identity.py"
        _ = test_file.write_text(
            """
from snektest import test

@test()
def first() -> None:
    pass

first.__name__ = "test_same"

@test()
def second() -> None:
    pass

second.__name__ = "test_same"
""".lstrip()
        )

        with assert_raises(CollectionError):
            _ = collect_tests_from_filters([FilterItem(str(test_file))])


@test()
def test_collection_rejects_empty_plan() -> None:
    """A successful command cannot silently report a zero-test run."""
    with tempfile.TemporaryDirectory() as tmp, assert_raises(CollectionError):
        _ = collect_tests_from_filters([FilterItem(tmp)])


@test()
def test_collection_rejects_run_fixture_identity_collision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_run_collision.py"
        _ = test_file.write_text(
            """
from collections.abc import Generator

from snektest import fixture, test

@fixture(scope="run")
def descriptor() -> Generator[str]:
    yield "first"

first_descriptor = descriptor

@fixture(scope="run")
def descriptor() -> Generator[str]:
    yield "second"

@test()
def test_one() -> None:
    pass
""".lstrip()
        )

        with assert_raises(CollectionError) as raised:
            _ = collect_tests_from_filters([FilterItem(str(test_file))])

    assert_in("Run fixture identity collision", str(raised.exception))


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

        file_path = cast(
            "PyFilePath", TypeAdapter(PyFilePath).validate_python(test_file)
        )
        filter_item = FilterItem(str(test_file))
        loop = asyncio.get_running_loop()

        queue: TestsQueue = TestsQueue()
        _ = load_tests_from_file(file_path, filter_item, queue, loop, mark=None)
        _ = await asyncio.wait_for(queue.get(), timeout=1)

        queue2: TestsQueue = TestsQueue()
        _ = load_tests_from_file(file_path, filter_item, queue2, loop, mark=None)
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

        file_path = cast(
            "PyFilePath", TypeAdapter(PyFilePath).validate_python(test_file)
        )
        loop = asyncio.get_running_loop()

        queue: TestsQueue = TestsQueue()
        _ = load_tests_from_file(
            file_path,
            FilterItem(f"{test_file}::test_other"),
            queue,
            loop,
            mark=None,
        )
        test_case = await asyncio.wait_for(queue.get(), timeout=1)
        assert_eq(test_case.name.func_name, "test_other")

        queue2: TestsQueue = TestsQueue()
        _ = load_tests_from_file(
            file_path,
            FilterItem(f"{test_file}::test_param[one]"),
            queue2,
            loop,
            mark=None,
        )
        parametrized_case = await asyncio.wait_for(queue2.get(), timeout=1)
        assert_eq(parametrized_case.name.params_part, "one")
        assert_eq(parametrized_case.param_values, (1,))

        queue2 = TestsQueue()
        _ = load_tests_from_file(
            file_path,
            FilterItem(f"{test_file}::test_param[does not match]"),
            queue2,
            loop,
            mark=None,
        )
        queue2.shutdown()
        with assert_raises(asyncio.QueueShutDown):
            _ = await queue2.get()


@test()
def test_load_tests_from_file_spec_loader_failure_raises_collection_error() -> None:
    def fake_spec(_name: object, _path: object) -> None:
        return None

    with assert_raises(CollectionError):
        queue: TestsQueue = TestsQueue()
        loop = asyncio.new_event_loop()
        try:
            _ = load_tests_from_file(
                cast(
                    "PyFilePath",
                    TypeAdapter(PyFilePath).validate_python(Path(__file__)),
                ),
                FilterItem(str(Path(__file__))),
                queue,
                loop,
                mark=None,
                spec_loader=fake_spec,
            )
        finally:
            loop.close()
