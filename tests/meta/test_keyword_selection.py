"""Public keyword selection across collection, local execution, and workers."""

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    fixture,
    load_fixture,
    test,
)

_MODES = [
    Param(value=(), name="local"),
    Param(value=("--workers", "2"), name="workers"),
    Param(value=("--collect-only",), name="collection"),
]


def _write_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[tool.snektest]\n", encoding="utf-8")
    (root / "tests" / "auth").mkdir(parents=True)
    (root / "tests" / "auth" / "test_first.py").write_text(
        dedent("""
            from snektest import Param, test

            @test([Param(value=1, name="red"), Param(value=2, name="blue")], mark="fast")
            def test_alpha(value: int) -> None:
                pass

            @test(mark="medium")
            def test_slow_connection() -> None:
                pass
        """),
        encoding="utf-8",
    )
    (root / "tests" / "test_second.py").write_text(
        dedent("""
            from snektest import test

            @test(mark="slow")
            def test_beta() -> None:
                pass
        """),
        encoding="utf-8",
    )
    (root / "test_empty.py").write_text("", encoding="utf-8")
    (root / "test_bad_import.py").write_text(
        "raise RuntimeError('import must not happen')\n", encoding="utf-8"
    )


@fixture
async def _project() -> AsyncGenerator[Path]:
    directory = await asyncio.to_thread(
        lambda: TemporaryDirectory(prefix="keyword-checkout-")
    )
    try:
        root = Path(directory.name)
        await asyncio.to_thread(_write_project, root)
        yield root
    finally:
        await asyncio.to_thread(directory.cleanup)


async def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    def run_command() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "snektest.cli", "--json-output", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    return await asyncio.to_thread(run_command)


@test(_MODES, mark="slow")
async def test_keyword_selection_is_global(mode: tuple[str, ...]) -> None:
    """An unmatched positional file does not invalidate a keyword selection."""
    root = await load_fixture(_project())

    completed = await _run(
        root,
        "tests/auth/test_first.py",
        "tests/test_second.py",
        "-k",
        "RED",
        *mode,
    )
    document = json.loads(completed.stdout)

    assert_eq(completed.returncode, 0)
    assert_eq(len(document["tests"]), 1)
    assert_in("test_alpha[red]", completed.stdout)
    assert_false("test_beta" in completed.stdout)


@test(
    [
        Param(value=("slow", 2), name="marker-or-function"),
        Param(value=("auth", 3), name="directory"),
        Param(value=("test_second.py", 1), name="module"),
        Param(value=("alpha and not blue", 1), name="case-exclusion"),
        Param(value=("(red or beta) and not blue", 2), name="boolean"),
        Param(value=("", 4), name="empty-expression"),
    ],
    mark="slow",
)
async def test_keyword_searches_collected_names(example: tuple[str, int]) -> None:
    root = await load_fixture(_project())
    expression, count = example

    completed = await _run(root, "tests", "--collect-only", "-k", expression)

    assert_eq(completed.returncode, 0)
    assert_eq(len(json.loads(completed.stdout)["tests"]), count)


@test(_MODES, mark="slow")
async def test_keyword_intersects_marker(mode: tuple[str, ...]) -> None:
    root = await load_fixture(_project())

    completed = await _run(
        root,
        "tests/auth/test_first.py",
        "tests/test_second.py",
        "-k",
        "slow",
        "--mark",
        "slow",
        *mode,
    )

    assert_eq(completed.returncode, 0)
    assert_eq(len(json.loads(completed.stdout)["tests"]), 1)
    assert_in("test_beta", completed.stdout)
    assert_false("test_slow_connection" in completed.stdout)


@test(_MODES, mark="slow")
async def test_keyword_preserves_repeated_occurrences(mode: tuple[str, ...]) -> None:
    root = await load_fixture(_project())

    completed = await _run(
        root,
        "tests/auth/test_first.py",
        "tests/test_second.py",
        "tests/auth/test_first.py",
        "-k",
        "red or beta",
        *mode,
    )

    assert_eq(completed.returncode, 0)
    assert_eq(
        [
            entry["name"].split("::", 1)[1]
            for entry in json.loads(completed.stdout)["tests"]
        ],
        [
            "test_alpha[red]",
            "test_beta",
            "test_alpha[red]",
        ],
    )


@test(_MODES, mark="slow")
async def test_empty_keyword_selection_requires_opt_in(mode: tuple[str, ...]) -> None:
    root = await load_fixture(_project())

    completed = await _run(root, "tests", "-k", "absent", *mode)

    assert_eq(completed.returncode, 2)
    assert_eq(json.loads(completed.stdout)["error"]["type"], "EmptyCollectionError")
    assert_in("absent", completed.stdout)


@test(_MODES, mark="slow")
async def test_allow_empty_keyword_selection(mode: tuple[str, ...]) -> None:
    root = await load_fixture(_project())

    completed = await _run(root, "tests", "-k", "absent", "--allow-empty", *mode)

    assert_eq(completed.returncode, 0)
    assert_eq(json.loads(completed.stdout)["tests"], [])


@test(
    [
        Param(value="tests/auth/test_first.py::missing", name="function"),
        Param(value="tests/auth/test_first.py::test_alpha[missing]", name="case"),
    ],
    _MODES,
    mark="slow",
)
async def test_keyword_cannot_hide_invalid_selector(
    selector: str, mode: tuple[str, ...]
) -> None:
    root = await load_fixture(_project())

    completed = await _run(root, selector, "-k", "absent", "--allow-empty", *mode)

    assert_eq(completed.returncode, 2)
    assert_eq(json.loads(completed.stdout)["error"]["type"], "CollectionError")


@test(mark="slow")
async def test_keyword_cannot_hide_empty_positional_file() -> None:
    root = await load_fixture(_project())

    completed = await _run(root, "tests", "test_empty.py", "-k", "red")

    assert_eq(completed.returncode, 2)
    assert_in("test_empty.py", json.loads(completed.stdout)["error"]["message"])


@test(_MODES, mark="slow")
async def test_invalid_keyword_fails_before_import(mode: tuple[str, ...]) -> None:
    root = await load_fixture(_project())

    completed = await _run(root, "test_bad_import.py", "-k", "alpha and", *mode)

    assert_eq(completed.returncode, 2)
    assert_in("Invalid keyword expression", completed.stdout)
    assert_false("import must not happen" in completed.stdout)


@test(mark="slow")
async def test_checkout_directory_is_not_searchable() -> None:
    root = await load_fixture(_project())

    completed = await _run(root, "tests", "-k", "keyword-checkout")

    assert_eq(completed.returncode, 2)
    assert_eq(json.loads(completed.stdout)["error"]["type"], "EmptyCollectionError")


@test(mark="slow")
async def test_absolute_paths_use_project_relative_names() -> None:
    root = await load_fixture(_project())

    completed = await _run(root, str(root / "tests"), "-k", "auth")

    assert_eq(completed.returncode, 0)
    assert_eq(len(json.loads(completed.stdout)["tests"]), 3)


@test(_MODES, mark="slow")
async def test_allow_empty_preserves_valid_keyword_matches(
    mode: tuple[str, ...],
) -> None:
    root = await load_fixture(_project())

    completed = await _run(
        root, "tests", "test_empty.py", "-k", "red", "--allow-empty", *mode
    )

    assert_eq(completed.returncode, 0)
    assert_eq(len(json.loads(completed.stdout)["tests"]), 1)


@test(mark="slow")
async def test_replacement_worker_reuses_keyword_selection() -> None:
    root = await load_fixture(_project())
    await asyncio.to_thread(
        (root / "test_replacement.py").write_text,
        dedent("""
            import os
            from snektest import test

            @test()
            def test_selected_crash() -> None:
                os._exit(7)

            @test()
            def test_excluded() -> None:
                raise RuntimeError("excluded body ran")

            @test()
            def test_selected_survivor() -> None:
                pass
        """),
        encoding="utf-8",
    )

    completed = await _run(
        root, "test_replacement.py", "-k", "selected", "--workers", "1"
    )
    document = json.loads(completed.stdout)

    assert_eq(completed.returncode, 1)
    assert_eq(document["passed"], 1)
    assert_eq(len(document["tests"]), 2)
    assert_in("test_selected_survivor", completed.stdout)
    assert_false("test_excluded" in completed.stdout)


@test(mark="slow")
async def test_keyword_baseline_update_preserves_unobserved_tests() -> None:
    root = await load_fixture(_project())
    benchmark_file = root / "test_perf.py"
    await asyncio.to_thread(
        benchmark_file.write_text,
        dedent("""
            from snektest import assert_benchmark, test

            @test(mark="fast")
            def test_alpha() -> None:
                with assert_benchmark(name="work", median_below=1.0,
                        median_regression_below=0.5, rounds=1, warmup=0) as timing:
                    for _ in timing.rounds:
                        pass

            @test(mark="fast")
            def test_beta() -> None:
                with assert_benchmark(name="work", median_below=1.0,
                        median_regression_below=0.5, rounds=1, warmup=0) as timing:
                    for _ in timing.rounds:
                        pass
        """),
        encoding="utf-8",
    )
    initial = await _run(
        root, "test_perf.py", "--update-benchmark-baseline", "baseline.json"
    )
    assert_eq(initial.returncode, 0)
    await asyncio.to_thread(
        benchmark_file.write_text,
        "from snektest import test\n@test()\ndef test_alpha() -> None:\n    pass\n",
        encoding="utf-8",
    )

    completed = await _run(
        root,
        "test_perf.py",
        "-k",
        "alpha",
        "--update-benchmark-baseline",
        "baseline.json",
    )
    snapshot = json.loads(
        await asyncio.to_thread((root / "baseline.json").read_text, encoding="utf-8")
    )

    assert_eq(completed.returncode, 0)
    assert_eq(len(snapshot["benchmarks"]), 1)
    assert_eq(snapshot["benchmarks"][0]["identity"]["function"], "test_beta")
