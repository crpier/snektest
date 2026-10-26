"""Python-native typing contracts evaluated by the optional ty checker."""

lazy import ast
lazy import asyncio
lazy import inspect
lazy import sys
lazy from collections.abc import Callable
lazy from contextlib import AbstractContextManager, suppress
lazy from dataclasses import dataclass
lazy from functools import wraps
lazy from importlib import import_module
lazy from pathlib import Path
lazy from tempfile import TemporaryDirectory
lazy from types import FunctionType, ModuleType
lazy from typing import Any, cast

lazy from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

lazy from snektest.models import AssertionFailure, BadRequestError, SnektestError
lazy from snektest.task_cleanup import cleanup_budget, defer_cancellation
lazy from snektest.utils import mark_test_function

_MAX_REPORT_BYTES = 4 * 1024 * 1024
_READ_BYTES = 65_536
_REAP_SECONDS = 5.0
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class _Position(BaseModel):
    model_config = ConfigDict(strict=True)
    line: int = Field(gt=0)


class _Positions(BaseModel):
    begin: _Position


class _Location(BaseModel):
    path: str
    positions: _Positions


class _Diagnostic(BaseModel):
    check_name: str
    description: str
    severity: str
    location: _Location


@dataclass(frozen=True)
class _ExpectedError:
    rule: str
    start: int
    end: int


def assert_type_error(rule: str, /) -> AbstractContextManager[None]:
    """Expect exactly one ty diagnostic in a static test's Python block.

    This marker is analyzed, never executed. It is not a runtime exception
    assertion and cannot be used inside an ordinary ``@test`` body.
    """
    _ = rule
    msg = "assert_type_error is only supported inside a @test_typing function"
    raise BadRequestError(msg)


def test_typing[F: Callable[..., object]](function: F) -> F:
    """Register a Python function for static analysis, without calling its body.

    Parameters represent symbolic typed inputs, not injected runtime fixtures.
    Static tests use the slow marker because they launch a checker subprocess.
    """

    @wraps(function)
    async def check() -> None:
        await _check(function)

    mark_test_function(check, (), ("slow",))
    return cast("F", check)


def _source(
    function: FunctionType,
) -> tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef, list[tuple[int, int]]]:
    filename = inspect.getsourcefile(function)
    if filename is None:
        msg = "A typing contract requires an accessible Python source file"
        raise BadRequestError(msg)
    path = Path(filename).resolve()
    module = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    other_contracts = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name != function.__name__
        and any(
            _reference(decorator, function.__globals__) is test_typing
            for decorator in node.decorator_list
        )
    ]
    for node in module.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function.__name__
        ):
            return path, node, other_contracts
    msg = "A typing contract must be a module-level Python function"
    raise BadRequestError(msg)


def _reference(expression: ast.expr, namespace: dict[str, Any]) -> object:
    if isinstance(expression, ast.Name):
        return namespace.get(expression.id)
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        module = namespace.get(expression.value.id)
        if isinstance(module, ModuleType) and module.__name__ in (
            "snektest",
            "snektest.type_contracts",
        ):
            return getattr(module, expression.attr, None)
    return None


def _expectations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    namespace: dict[str, Any],
) -> list[_ExpectedError]:
    expectations: list[_ExpectedError] = []
    for block in ast.walk(node):
        if not isinstance(block, ast.With):
            continue
        for item in block.items:
            call = item.context_expr
            if not (
                isinstance(call, ast.Call)
                and _reference(call.func, namespace) is assert_type_error
            ):
                continue
            if (
                len(block.items) != 1
                or len(call.args) != 1
                or call.keywords
                or not isinstance(call.args[0], ast.Constant)
                or not isinstance(call.args[0].value, str)
                or not call.args[0].value
            ):
                msg = "assert_type_error requires one literal ty rule name and its own with block"
                raise BadRequestError(msg)
            expectations.append(
                _ExpectedError(
                    call.args[0].value, block.lineno, block.end_lineno or block.lineno
                )
            )
    return expectations


def _project_root(path: Path) -> Path:
    for parent in path.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    package_root = path.parent
    while (package_root / "__init__.py").is_file():
        package_root = package_root.parent
    return package_root


async def _read_report(stream: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(_READ_BYTES):
        size += len(chunk)
        if size > _MAX_REPORT_BYTES:
            msg = "ty output exceeded the 4 MiB report limit"
            raise SnektestError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


@defer_cancellation
async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    async with asyncio.timeout(min(_REAP_SECONDS, cleanup_budget.get())):
        await process.wait()


async def _invoke(command: list[str], root: Path) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if process.stdout is None or process.stderr is None:
            msg = "ty output pipes could not be opened"
            raise SnektestError(msg)
        async with asyncio.timeout(cleanup_budget.get()), asyncio.TaskGroup() as group:
            stdout = group.create_task(_read_report(process.stdout))
            stderr = group.create_task(_read_report(process.stderr))
            status = await process.wait()
        return status, stdout.result(), stderr.result()
    finally:
        await _reap(process)


async def _diagnostics(path: Path) -> tuple[Path, list[_Diagnostic]]:
    try:
        ty = import_module("ty")
    except ModuleNotFoundError as error:
        msg = "Typing contracts require ty; install snektest[typing]"
        raise SnektestError(msg) from error
    executable = str(ty.find_ty_bin())
    root = _project_root(path)
    with TemporaryDirectory(prefix="snektest-typing-") as directory:
        scratch = Path(directory)
        overlay = scratch / "snektest-stubs"
        overlay.mkdir()
        (overlay / "py.typed").write_text("partial\n", encoding="utf-8")
        stub = Path(__file__).with_name("__init__.pyi").read_text(encoding="utf-8")
        stub = stub.replace(
            "from typing import no_type_check as test_typing\n",
            "from snektest.type_contracts import test_typing as test_typing\n",
        )
        (overlay / "__init__.pyi").write_text(stub, encoding="utf-8")
        config = scratch / "ty.toml"
        config.write_text('[rules]\nall = "error"\n', encoding="utf-8")
        command = [
            executable,
            "check",
            "--config-file",
            str(config),
            "--python",
            sys.executable,
            "--extra-search-path",
            str(scratch),
            "--extra-search-path",
            str(_PACKAGE_ROOT),
            "--extra-search-path",
            str(root),
            "--output-format",
            "gitlab",
            "--no-respect-ignore-files",
            "--no-force-exclude",
            str(path),
        ]
        status, stdout, stderr = await _invoke(command, root)
    if status not in (0, 1):
        msg = f"ty invocation failed with status {status}: {stderr.decode(errors='replace')[:2000]}"
        raise SnektestError(msg)
    diagnostics = [
        item
        for item in TypeAdapter(list[_Diagnostic]).validate_json(stdout)
        if item.severity in ("major", "critical", "blocker")
    ]
    if (status == 1) != bool(diagnostics):
        msg = "ty exit status disagrees with its error diagnostics"
        raise SnektestError(msg)
    return root, diagnostics


async def _check(function: Callable[..., object]) -> None:
    if not inspect.isfunction(function):
        msg = "A typing contract requires a Python function"
        raise BadRequestError(msg)
    path, node, other_contracts = _source(function)
    expected = _expectations(node, function.__globals__)
    root, diagnostics = await _diagnostics(path)
    observed: list[list[_Diagnostic]] = [[] for _ in expected]
    unexpected: list[_Diagnostic] = []
    for diagnostic in diagnostics:
        if (root / diagnostic.location.path).resolve() != path:
            msg = f"ty reported an unexpected source: {diagnostic.location.path}"
            raise SnektestError(msg)
        line = diagnostic.location.positions.begin.line
        if any(start <= line <= end for start, end in other_contracts):
            continue
        if diagnostic.check_name == "unresolved-import":
            msg = f"Typing contract environment is unavailable: {diagnostic.description[:2000]}"
            raise SnektestError(msg)
        regions = [
            index
            for index, region in enumerate(expected)
            if region.start <= line <= region.end
        ]
        if regions:
            innermost = min(
                regions, key=lambda index: expected[index].end - expected[index].start
            )
            observed[innermost].append(diagnostic)
        else:
            unexpected.append(diagnostic)
    problems = [item.description for item in unexpected]
    for region, actual in zip(expected, observed, strict=True):
        if len(actual) != 1 or actual[0].check_name != region.rule:
            found = ", ".join(item.check_name for item in actual) or "no diagnostics"
            problems.append(
                f"line {region.start}: expected exactly one {region.rule}; found {found}"
            )
    if problems:
        raise AssertionFailure("Typing contract failed:\n" + "\n".join(problems)[:8000])
