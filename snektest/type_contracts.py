"""Python-native typing contracts evaluated by the optional ty checker."""

lazy import ast
lazy import asyncio
lazy import inspect
lazy import symtable
lazy import sys
lazy import tomllib
lazy from collections.abc import Callable, Generator, Iterator
lazy from contextlib import AbstractContextManager, contextmanager, suppress
lazy from contextvars import ContextVar
lazy from dataclasses import dataclass
lazy from functools import wraps
lazy from hashlib import sha256
lazy from importlib import import_module
lazy from pathlib import Path
lazy from tempfile import TemporaryDirectory
lazy from types import ModuleType
lazy from typing import Any, cast

lazy from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

lazy from snektest.decorators import fixture, load_fixture
lazy from snektest.models import AssertionFailure, BadRequestError, SnektestError
lazy from snektest.task_cleanup import cleanup_budget, defer_cancellation
lazy from snektest.utils import mark_test_function

_MAX_REPORT_BYTES = 4 * 1024 * 1024
_READ_BYTES = 65_536
_REAP_SECONDS = 5.0
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_MARKER_NOTE = "snektest static assertion reference"
_configuration: ContextVar[Path | None] = ContextVar(
    "typing_configuration", default=None
)
type _Span = tuple[tuple[int, int], tuple[int, int]]


class _Position(BaseModel):
    model_config = ConfigDict(strict=True)
    line: int = Field(gt=0)
    column: int = Field(gt=0)


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
    line: int
    start: tuple[int, int]
    end: tuple[int, int]
    reference: _Span


@dataclass(frozen=True)
class _ContractSource:
    namespace: dict[str, Any]
    fingerprint: bytes
    path: Path
    node: ast.FunctionDef | ast.AsyncFunctionDef
    other_contracts: list[tuple[int, int]]
    scopes: dict[tuple[str, int], symtable.SymbolTable]
    lines: list[str]
    imports: list[_Span]


@dataclass(frozen=True)
class _CachedReport:
    path: Path
    fingerprint: bytes
    configuration: tuple[Path, bytes] | None
    root: Path
    diagnostics: list[_Diagnostic]


@dataclass
class _ReportCache:
    report: _CachedReport | None = None


@fixture(scope="session")
def _report_cache() -> Generator[_ReportCache]:
    # A registry belongs to one run/worker incarnation. Retain at most one file,
    # not an unbounded map of every report seen by a large suite.
    yield _ReportCache()


async def _cached_diagnostics(
    source: _ContractSource,
) -> tuple[Path, list[_Diagnostic]]:
    cache = load_fixture(_report_cache())
    configuration = _configuration_identity()
    report = cache.report
    if report is None or (report.path, report.fingerprint, report.configuration) != (
        source.path,
        source.fingerprint,
        configuration,
    ):
        root, diagnostics = await _diagnostics(source.path)
        if _configuration_identity() != configuration:
            msg = "Typing checker configuration changed during analysis"
            raise SnektestError(msg)
        report = _CachedReport(
            source.path, source.fingerprint, configuration, root, diagnostics
        )
        cache.report = report
    return report.root, report.diagnostics


@contextmanager
def typing_configuration(path: Path | None) -> Generator[None]:
    """Scope one explicit native ty configuration to a run/worker incarnation."""
    token = _configuration.set(path.resolve() if path is not None else None)
    try:
        yield
    finally:
        _configuration.reset(token)


def _configuration_identity() -> tuple[Path, bytes] | None:
    path = _configuration.get()
    return None if path is None else (path, sha256(path.read_bytes()).digest())


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

    fingerprint = _fingerprint(function)

    @wraps(function)
    async def check() -> None:
        await _check(function, fingerprint)

    mark_test_function(check, (), ("slow",))
    return cast("F", check)


def _fingerprint(function: Callable[..., object]) -> bytes | None:
    if not inspect.isfunction(function):
        return None
    filename = inspect.getsourcefile(function)
    if filename is None:
        return None
    try:
        return sha256(Path(filename).read_bytes()).digest()
    except OSError:
        return None


def _verify_source(path: Path, content: bytes, fingerprint: bytes) -> None:
    if sha256(content).digest() != fingerprint:
        msg = f"Typing contract source changed after collection: {path}"
        raise SnektestError(msg)


def _source(
    function: Callable[..., object], fingerprint: bytes | None
) -> _ContractSource:
    if not inspect.isfunction(function):
        msg = "A typing contract requires a Python function"
        raise BadRequestError(msg)
    if fingerprint is None:
        msg = "Typing contract source was unavailable during collection"
        raise SnektestError(msg)
    filename = inspect.getsourcefile(function)
    if filename is None:
        msg = "A typing contract requires an accessible Python source file"
        raise BadRequestError(msg)
    path = Path(filename).resolve()
    content = path.read_bytes()
    _verify_source(path, content, fingerprint)
    text = content.decode("utf-8-sig")
    module = ast.parse(text, filename=str(path))
    tables = symtable.symtable(text, str(path), "exec")
    scopes: dict[tuple[str, int], symtable.SymbolTable] = {}
    pending = [tables]
    while pending:
        table = pending.pop()
        if table.get_type() in (
            symtable.SymbolTableType.FUNCTION,
            symtable.SymbolTableType.CLASS,
        ):
            scopes[table.get_name(), table.get_lineno()] = table
        pending.extend(table.get_children())
    selected = next(
        (
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function.__code__.co_name
            and min([node.lineno, *(item.lineno for item in node.decorator_list)])
            == function.__code__.co_firstlineno
        ),
        None,
    )
    if selected is None:
        msg = "A typing contract must be a module-level Python function"
        raise BadRequestError(msg)
    imports = _local_marker_imports(module)
    namespace = {
        symbol.get_name(): imports.get(symbol.get_name())
        for symbol in tables.get_symbols()
        if symbol.is_imported() and not symbol.is_assigned()
    }
    if len(selected.decorator_list) != 1 or (
        _reference(selected.decorator_list[0], namespace) is not test_typing
    ):
        msg = "A typing contract requires a single bare @test_typing decorator"
        raise BadRequestError(msg)
    other_contracts = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node is not selected
        and any(
            _reference(decorator, namespace) is test_typing
            for decorator in node.decorator_list
        )
    ]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    imports = [
        _span(lines, alias)
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module in ("snektest", "snektest.type_contracts")
        for alias in node.names
        if alias.name == "assert_type_error"
    ]
    return _ContractSource(
        namespace,
        fingerprint,
        path,
        selected,
        other_contracts,
        scopes,
        lines,
        imports,
    )


def _reference(expression: ast.expr, namespace: dict[str, Any]) -> object:
    if isinstance(expression, ast.Name):
        return namespace.get(expression.id)
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        module = namespace.get(expression.value.id)
        if isinstance(module, ModuleType) and module.__name__ in (
            "snektest",
            "snektest.type_contracts",
        ):
            return {
                "assert_type_error": assert_type_error,
                "test_typing": test_typing,
            }.get(expression.attr)
    return None


def _local_marker_imports(node: ast.AST) -> dict[str, object]:
    """Only unambiguous literal imports establish local marker identities."""
    imports: dict[str, list[object]] = {}
    pending = list(ast.iter_child_nodes(node))
    while pending:
        child = pending.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.ImportFrom):
            for alias in child.names:
                binding = (
                    {
                        "assert_type_error": assert_type_error,
                        "test_typing": test_typing,
                    }.get(alias.name)
                    if child.level == 0
                    and child.module in ("snektest", "snektest.type_contracts")
                    else None
                )
                imports.setdefault(alias.asname or alias.name, []).append(binding)
        elif isinstance(child, ast.Import):
            for alias in child.names:
                binding = sys.modules["snektest"] if alias.name == "snektest" else None
                imports.setdefault(alias.asname or alias.name.split(".")[0], []).append(
                    binding
                )
        pending.extend(ast.iter_child_nodes(child))
    return {name: values[0] for name, values in imports.items() if len(values) == 1}


def _scoped_blocks(
    node: ast.AST,
    scopes: dict[tuple[str, int], symtable.SymbolTable],
    globals_namespace: dict[str, Any],
    namespace: dict[str, Any],
    closure: dict[str, Any],
) -> Iterator[tuple[ast.With, dict[str, Any]]]:
    """Use Python's symbol tables, not name spelling, to resolve marker scopes."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        table = scopes[node.name, node.lineno]
        namespace = dict(globals_namespace)
        imports = _local_marker_imports(node)
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if (
                symbol.is_imported()
                and not symbol.is_assigned()
                and not symbol.is_parameter()
            ):
                namespace[name] = imports.get(name)
            elif symbol.is_local() or symbol.is_assigned():
                namespace[name] = None
            elif symbol.is_free():
                namespace[name] = closure.get(name)
        if not isinstance(node, ast.ClassDef):
            closure = namespace
    if isinstance(node, ast.With):
        yield node, namespace
    for child in ast.iter_child_nodes(node):
        yield from _scoped_blocks(child, scopes, globals_namespace, namespace, closure)


def _expectations(source: _ContractSource) -> list[_ExpectedError]:
    namespace = source.namespace
    expectations: list[_ExpectedError] = []
    for block, bindings in _scoped_blocks(
        source.node, source.scopes, namespace, namespace, namespace
    ):
        for item in block.items:
            call = item.context_expr
            _reject_dynamic_marker(call, bindings)
            if not (
                isinstance(call, ast.Call)
                and _reference(call.func, bindings) is assert_type_error
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
            first: ast.stmt | ast.expr = block.body[0]
            if isinstance(first, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                first = next(iter(first.decorator_list), first)
            expectations.append(
                _ExpectedError(
                    call.args[0].value,
                    block.lineno,
                    _coordinate(source.lines, first.lineno, first.col_offset),
                    _coordinate(
                        source.lines,
                        block.end_lineno or block.lineno,
                        block.end_col_offset or 0,
                    ),
                    _span(source.lines, call.func),
                )
            )
    return expectations


def _reject_dynamic_marker(expression: ast.expr, bindings: dict[str, Any]) -> None:
    if not isinstance(expression, ast.Call) or not isinstance(
        expression.func, ast.Call
    ):
        return
    lookup = expression.func
    if not (
        isinstance(lookup.func, ast.Name)
        and lookup.func.id == "getattr"
        and len(lookup.args) > 1
    ):
        return
    module = _reference(lookup.args[0], bindings)
    attribute = lookup.args[1]
    if (
        isinstance(module, ModuleType)
        and module.__name__ in ("snektest", "snektest.type_contracts")
        and (
            not isinstance(attribute, ast.Constant)
            or attribute.value == "assert_type_error"
        )
    ):
        msg = "Unsupported dynamic marker lookup; use a direct Snektest import"
        raise BadRequestError(msg)


def _coordinate(lines: list[str], line: int, byte_column: int) -> tuple[int, int]:
    """Convert AST UTF-8 offsets to ty's one-based Unicode columns."""
    prefix = lines[line - 1].encode("utf-8")[:byte_column]
    return line, len(prefix.decode("utf-8")) + 1


def _span(lines: list[str], node: ast.expr | ast.alias) -> _Span:
    return (
        _coordinate(lines, node.lineno, node.col_offset),
        _coordinate(lines, node.end_lineno or node.lineno, node.end_col_offset or 0),
    )


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


async def _discard_report(stream: asyncio.StreamReader) -> None:
    while await stream.read(_READ_BYTES):
        pass


@defer_cancellation
async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    async with (
        asyncio.timeout(min(_REAP_SECONDS, cleanup_budget.get())),
        asyncio.TaskGroup() as group,
    ):
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                group.create_task(_discard_report(stream))
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
    except ExceptionGroup as error:
        if len(error.exceptions) == 1 and isinstance(
            error.exceptions[0], SnektestError
        ):
            raise error.exceptions[0] from None
        raise
    finally:
        await _reap(process)


def _python_arguments(config: Path) -> list[str]:
    # Only an explicit environment.python opts out of the current interpreter.
    # Leave native syntax/type validation to ty, including malformed files.
    try:
        settings = tomllib.loads(config.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError, UnicodeDecodeError:
        return ["--python", sys.executable]
    environment = settings.get("environment")
    if isinstance(environment, dict) and "python" in environment:
        return []
    return ["--python", sys.executable]


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
        (overlay / "type_contracts.pyi").write_text(
            "from collections.abc import Callable\n"
            "from contextlib import AbstractContextManager\n"
            "from warnings import deprecated\n"
            "def test_typing[F: Callable[..., object]](function: F) -> F: ...\n"
            f"@deprecated({_MARKER_NOTE!r})\n"
            "def assert_type_error(rule: str, /) -> AbstractContextManager[None]: ...\n",
            encoding="utf-8",
        )
        config = _configuration.get()
        if config is None:
            config = scratch / "ty.toml"
            config.write_text('[rules]\nall = "error"\n', encoding="utf-8")
        command = [
            executable,
            "check",
            "--config-file",
            str(config),
            "--error",
            "deprecated",
            "--error",
            "type-assertion-failure",
            "--error",
            "unresolved-import",
            *_python_arguments(config),
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


def _marker_reference(
    diagnostic: _Diagnostic, source: _ContractSource, expected: list[_ExpectedError]
) -> bool:
    """Let ty identify helper values, including aliases our syntax disallows.

    Only the temporary checker stub is deprecated. Ordinary user checks and
    runtime imports never see this internal reference-verification diagnostic.
    """
    if diagnostic.check_name != "deprecated" or not diagnostic.description.endswith(
        f": {_MARKER_NOTE}"
    ):
        return False
    position = diagnostic.location.positions.begin
    point = position.line, position.column
    allowed = [*source.imports, *(region.reference for region in expected)]
    if not any(start <= point < end for start, end in allowed):
        msg = (
            f"{source.path}:{position.line}: assertion markers require a direct "
            "Snektest import and a literal assert_type_error with block; "
            "copied or indirect marker values are unsupported"
        )
        raise BadRequestError(msg)
    return True


async def _check(function: Callable[..., object], fingerprint: bytes | None) -> None:
    source = _source(function, fingerprint)
    path = source.path
    expected = _expectations(source)
    root, diagnostics = await _cached_diagnostics(source)
    _verify_source(path, path.read_bytes(), source.fingerprint)
    observed: list[list[_Diagnostic]] = [[] for _ in expected]
    unexpected: list[_Diagnostic] = []
    for diagnostic in diagnostics:
        if (root / diagnostic.location.path).resolve() != path:
            msg = f"ty reported an unexpected source: {diagnostic.location.path}"
            raise SnektestError(msg)
        line = diagnostic.location.positions.begin.line
        if any(start <= line <= end for start, end in source.other_contracts):
            continue
        if diagnostic.check_name == "unresolved-import":
            msg = f"Typing contract environment is unavailable: {diagnostic.description[:2000]}"
            raise SnektestError(msg)
        point = (line, diagnostic.location.positions.begin.column)
        if _marker_reference(diagnostic, source, expected):
            continue
        regions = [
            index
            for index, region in enumerate(expected)
            if region.start <= point < region.end
        ]
        if regions:
            innermost = max(regions, key=lambda index: expected[index].start)
            observed[innermost].append(diagnostic)
        else:
            unexpected.append(diagnostic)
    problems = [
        f"{path}:{item.location.positions.begin.line}:{item.location.positions.begin.column}: {item.description}"
        for item in unexpected
    ]
    for region, actual in zip(expected, observed, strict=True):
        if len(actual) != 1 or actual[0].check_name != region.rule:
            found = ", ".join(item.check_name for item in actual) or "no diagnostics"
            problems.append(
                f"{path}:{region.line}: expected exactly one {region.rule}; found {found}"
            )
    if problems:
        raise AssertionFailure("Typing contract failed:\n" + "\n".join(problems)[:8000])
