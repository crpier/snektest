"""Subprocess regressions for deterministic, import-safe collection."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

from snektest import (
    Param,
    assert_eq,
    assert_raises,
    fail,
    load_fixture,
    test,
)
from snektest.cli import run_tests_programmatic
from snektest.models import CollectionError, FilterItem
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test(
    [Param(value=None, name="local"), Param(value="1", name="workers")],
    mark="slow",
)
def test_import_output_and_warnings_do_not_corrupt_json(
    worker_count: str | None,
) -> None:
    """Collection diagnostics stay outside the JSON document and test result."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import warnings

            from snektest import test

            print("import output")
            warnings.warn("import warning", stacklevel=1)

            @test()
            def test_passes() -> None:
                pass
        """),
        name=f"test_import_output_{worker_count or 'local'}",
    )
    worker_args = [] if worker_count is None else ["--workers", worker_count]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest.cli",
            "--json-output",
            *worker_args,
            str(test_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    summary = cast("dict[str, Any]", json.loads(result.stdout))

    assert_eq(result.returncode, 0)
    assert_eq(summary["passed"], 1)
    assert_eq(summary["tests"][0]["warnings"], [])
    assert_eq(summary["collection_output"], "import output\n")
    assert_eq(len(summary["collection_warnings"]), 1)


@test(mark="medium")
async def test_package_relative_imports_work_for_absolute_and_relative_filters() -> (
    None
):
    """Equivalent path spellings collect the same package module successfully."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
        package_dir = Path(tmp) / "relative_package"
        package_dir.mkdir()
        _ = (package_dir / "__init__.py").write_text("")
        _ = (package_dir / "helper.py").write_text("VALUE = 7\n")
        test_file = create_test_file(
            package_dir,
            dedent("""
                from snektest import assert_eq, test

                from .helper import VALUE

                @test()
                def test_relative_import() -> None:
                    assert_eq(VALUE, 7)
            """),
            name="test_relative_import",
        )
        relative_file = os.path.relpath(test_file, start=Path.cwd())

        try:
            absolute_run = await run_tests_programmatic([FilterItem(str(test_file))])
            relative_run = await run_tests_programmatic([FilterItem(relative_file)])
        except CollectionError as error:
            fail(f"Package collection failed: {error}")
            return

        assert_eq(absolute_run.passed, 1)
        assert_eq(relative_run.passed, 1)
        assert_eq(
            absolute_run.test_results[0].name.resolved_file_path,
            relative_run.test_results[0].name.resolved_file_path,
        )


@test(mark="medium")
async def test_failed_import_can_be_corrected_and_recollected() -> None:
    """A failed module import leaves no poison in a later in-process run."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        'raise RuntimeError("broken import")\n',
        name="test_retry_import",
    )

    with assert_raises(CollectionError):
        _ = await run_tests_programmatic([FilterItem(str(test_file))])

    _ = test_file.write_text(
        dedent("""
            from snektest import test

            @test()
            def test_fixed() -> None:
                pass
        """)
    )
    corrected_run = await run_tests_programmatic([FilterItem(str(test_file))])

    assert_eq(corrected_run.passed, 1)


@test(mark="medium")
async def test_modules_import_once_per_run_and_fresh_across_runs() -> None:
    """Overlapping filters share one import, while later runs reload the file."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    event_file = tmp_dir / "collection-import-events"
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import Generator
            from pathlib import Path

            from snektest import assert_eq, fixture, load_fixture, test

            with Path({str(event_file)!r}).open("a") as events:
                _ = events.write("imported\\n")

            @fixture(scope="run")
            def run_value() -> Generator[int]:
                yield 1

            @test()
            def test_cached_per_run() -> None:
                assert_eq(load_fixture(run_value()), 1)
        """),
        name="test_collection_cache",
    )
    selected = FilterItem(str(test_file))

    overlapping_run = await run_tests_programmatic([selected, selected])
    later_run = await run_tests_programmatic([selected])

    assert_eq(overlapping_run.passed, 2)
    assert_eq(later_run.passed, 1)
    assert_eq(event_file.read_text().splitlines(), ["imported", "imported"])


@test(mark="medium")
async def test_imported_decorated_function_is_not_collected_as_local() -> None:
    """A consumer module cannot claim a decorated function imported from a peer."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    package_dir = tmp_dir / "function_ownership_package"
    package_dir.mkdir()
    event_file = package_dir / "source-import-events"
    _ = (package_dir / "__init__.py").write_text("")
    _ = create_test_file(
        package_dir,
        dedent(f"""
            from pathlib import Path

            from snektest import test

            with Path({str(event_file)!r}).open("a") as events:
                _ = events.write("source-import\\n")

            @test()
            def test_source() -> None:
                pass
        """),
        name="test_source",
    )
    _ = create_test_file(
        package_dir,
        dedent("""
            from snektest import test

            from .test_source import test_source

            @test()
            def test_consumer() -> None:
                _ = test_source
        """),
        name="test_consumer",
    )

    summary = await run_tests_programmatic([FilterItem(str(package_dir))])

    assert_eq(summary.passed, 2)
    assert_eq(event_file.read_text().splitlines(), ["source-import"])
    assert_eq(
        [result.name.func_name for result in summary.test_results],
        ["test_consumer", "test_source"],
    )


@test(mark="medium")
async def test_all_imports_finish_before_any_test_body_runs() -> None:
    """Execution starts only after every selected module import has completed."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    suite_dir = tmp_dir / "collection-barrier"
    suite_dir.mkdir()
    event_file = suite_dir / "events"
    for suffix in ("a", "b"):
        _ = create_test_file(
            suite_dir,
            dedent(f"""
                from pathlib import Path

                from snektest import assert_eq, test

                EVENTS = Path({str(event_file)!r})
                with EVENTS.open("a") as events:
                    _ = events.write("import-{suffix}\\n")

                @test()
                def test_{suffix}() -> None:
                    assert_eq(
                        EVENTS.read_text().splitlines(),
                        ["import-a", "import-b"],
                    )
            """),
            name=f"test_{suffix}",
        )

    summary = await run_tests_programmatic([FilterItem(str(suite_dir))])

    assert_eq(summary.passed, 2)
    assert_eq(
        [result.name.func_name for result in summary.test_results],
        ["test_a", "test_b"],
    )


@test(mark="medium")
async def test_same_named_modules_remain_distinct_and_ordered() -> None:
    """Canonical paths isolate equal basenames and sort them deterministically."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    suite_dir = tmp_dir / "same-named-modules"
    for directory_name, value in (("left", 1), ("right", 2)):
        directory = suite_dir / directory_name
        directory.mkdir(parents=True)
        _ = create_test_file(
            directory,
            dedent(f"""
                from snektest import assert_eq, test

                VALUE = {value}

                @test()
                def test_identity() -> None:
                    assert_eq(VALUE, {value})
            """),
            name="test_same",
        )

    summary = await run_tests_programmatic([FilterItem(str(suite_dir))])

    assert_eq(summary.passed, 2)
    assert_eq(
        [result.name.file_path.parent.name for result in summary.test_results],
        ["left", "right"],
    )
