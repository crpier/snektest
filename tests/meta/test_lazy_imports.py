"""Consumer-facing import boundaries and forced-lazy execution contracts."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from snektest import Param, assert_eq, test


def _python(source: str, *, mode: str = "normal") -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            f"lazy_imports={mode}",
            "-c",
            f"import sys; sys.path.insert(0, {str(Path.cwd())!r});\n" + dedent(source),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )


@test([Param("normal", name="normal"), Param("all", name="all")], mark="slow")
def test_package_import_defers_execution_and_optional_dependencies(mode: str) -> None:
    """Importing the public facade does not initialize test execution tools."""
    completed = _python(
        """
        import json
        before = set(sys.modules)
        import snektest
        _ = snektest.__all__
        print(json.dumps(sorted(
            name for name in set(sys.modules) - before
            if name.split('.')[0] in {
                'asyncio', 'hypothesis', 'multiprocessing', 'rich', 'schemathesis'
            }
        )))
    """,
        mode=mode,
    )
    assert_eq(completed.returncode, 0, msg=completed.stderr)
    assert_eq(json.loads(completed.stdout), [])


@test(mark="slow")
def test_unused_explicit_lazy_import_fails_during_collection() -> None:
    """An unresolved test-module import cannot become a later body error."""
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "test_lazy_missing.py"
        path.write_text(
            dedent("""
            lazy import snektest_missing_dependency_93
            from snektest import test

            @test(mark="fast")
            def example():
                pass
        """)
        )
        completed = _python(f"""
            import json
            from snektest.cli import main
            sys.argv = ['snektest', '--json-output', '--collect-only', {str(path)!r}]
            main()
        """)
    assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(document["kind"], "error")
    assert_eq(document["error"]["category"], "collection")


@test(
    [
        Param("author", name="ordinary-test"),
        Param("--help", name="help"),
        Param("--version", name="version"),
    ],
    mark="slow",
)
def test_lightweight_entry_points_do_not_load_property_or_schema_engines(
    action: str,
) -> None:
    """Ordinary decorators and informational CLI actions do not need engines."""
    completed = _python(f"""
        import json
        import contextlib
        import io
        before = set(sys.modules)
        if {action!r} == 'author':
            from snektest import test
            @test(mark='fast')
            def example():
                pass
        else:
            from snektest.cli import main
            sys.argv = ['snektest', {action!r}]
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    main()
                except SystemExit as error:
                    if error.code != 0:
                        raise
        print(json.dumps(sorted(
            name for name in set(sys.modules) - before
            if name.split('.')[0] in {{'hypothesis', 'schemathesis'}}
            or name in {{'snektest.execution', 'snektest.parallel'}}
        )))
    """)
    assert_eq(completed.returncode, 0, msg=completed.stderr)
    assert_eq(json.loads(completed.stdout), [])


@test(
    [Param("normal", name="normal"), Param("all", name="all")],
    [Param(None, name="local"), Param(1, name="worker")],
    mark="slow",
)
def test_lazy_collection_runs_property_tests_and_run_fixtures(
    mode: str, workers: int | None
) -> None:
    """The CLI initializes Hypothesis and shares run descriptors in either mode."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        package = root / "cases"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "support.py").write_text(
            dedent("""
            import warnings
            print('support import')
            warnings.warn('support warning', stacklevel=1)
        """)
        )
        path = package / "test_property.py"
        path.write_text(
            dedent("""
            import sys
            from collections.abc import AsyncGenerator, Generator
            from hypothesis import settings, strategies as st
            from snektest import assert_eq, fixture, load_fixture, test, test_hypothesis
            lazy from . import support

            @fixture(scope='run')
            def descriptor() -> Generator[dict[str, int]]:
                yield {'value': 7}

            @fixture
            async def resource() -> AsyncGenerator[int]:
                value = load_fixture(descriptor())
                yield value['value']

            @settings(max_examples=1, database=None, deadline=None)
            @test_hypothesis(st.just(7), mark='fast')
            async def property_case(expected: int) -> None:
                assert_eq(sys.get_lazy_imports(), __MODE__)
                assert_eq(await load_fixture(resource()), expected)

            @settings(max_examples=1, database=None, deadline=None)
            @test_hypothesis(st.just(7), mark='fast')
            def synchronous_property_case(expected: int) -> None:
                assert_eq(sys.get_lazy_imports(), __MODE__)
                assert_eq(load_fixture(descriptor())['value'], expected)

            @test(mark='fast')
            def ordinary_case() -> None:
                assert_eq(sys.get_lazy_imports(), __MODE__)
                assert_eq(load_fixture(descriptor()), {'value': 7})
        """).replace("__MODE__", repr(mode))
        )
        worker_args = [] if workers is None else ["--workers", str(workers)]
        completed = _python(
            f"""
            from snektest.cli import main
            sys.argv = ['snektest', '--json-output', *{worker_args!r}, {str(path)!r}]
            main()
        """,
            mode=mode,
        )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    document = json.loads(completed.stdout)
    assert_eq(document["passed"], 3)
    assert_eq(document["collection_output"], "support import\n")
    assert_eq(len(document["collection_warnings"]), 1)
    assert_eq([item["warnings"] for item in document["tests"]], [[], [], []])


@test(
    [
        Param("success", name="success"),
        Param("collection-error", name="collection-error"),
    ],
    mark="slow",
)
def test_collection_restores_and_delegates_the_callers_import_filter(
    outcome: str,
) -> None:
    """The scoped policy preserves embedding settings, including on import errors."""
    broken = outcome == "collection-error"
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "outside_helper.py").write_text(
            dedent("""
            lazy import pathlib
            binding_type = type(globals()['pathlib']).__name__
        """)
        )
        path = root / "test_filter.py"
        path.write_text(
            dedent(f"""
            import outside_helper
            from snektest import assert_eq, test
            {'raise RuntimeError("broken collection")' if broken else ""}

            @test(mark='fast')
            def example():
                assert_eq(outside_helper.binding_type, 'module')
        """)
        )
        completed = _python(
            f"""
            import asyncio
            import json
            from snektest.cli import run_tests_programmatic
            from snektest.models import CollectionError, FilterItem
            from snektest.reporting import NullRunReporter
            sys.path.insert(0, {str(root)!r})
            def application_filter(importer, name, fromlist):
                return importer != 'outside_helper'
            sys.set_lazy_imports_filter(application_filter)
            try:
                result = asyncio.run(run_tests_programmatic(
                    [FilterItem({str(path)!r})], reporter=NullRunReporter()
                ))
                outcome = result.passed
            except CollectionError:
                outcome = 'collection-error'
            print(json.dumps({{
                'restored': sys.get_lazy_imports_filter() is application_filter,
                'mode': sys.get_lazy_imports(),
                'outcome': outcome,
            }}))
        """,
            mode="all",
        )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(
        json.loads(completed.stdout),
        {
            "restored": True,
            "mode": "all",
            "outcome": "collection-error" if broken else 1,
        },
    )


@test(mark="medium")
def test_runtime_module_imports_use_explicit_lazy_syntax() -> None:
    """Runtime sources, including bundled examples, retain the import policy."""
    offenders: list[str] = []
    for path in sorted(Path("snektest").rglob("*.py")):
        tree = ast.parse(path.read_text())

        def inspect_imports(
            node: ast.AST, *, restricted: bool = False, source: Path = path
        ) -> None:
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Try,
                    ast.TryStar,
                ),
            ):
                restricted = True
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not restricted:
                future = (
                    isinstance(node, ast.ImportFrom) and node.module == "__future__"
                )
                if not future and not node.is_lazy:
                    offenders.append(f"{source}:{node.lineno}")
            for child in ast.iter_child_nodes(node):
                inspect_imports(child, restricted=restricted)

        inspect_imports(tree)
    assert_eq(offenders, [])
