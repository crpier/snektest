"""Embedded documentation and examples for coding agents."""

from importlib.resources import files

from snektest.models import BadRequestError

AGENT_DOCS = """# snektest agent guide

Snektest requires Python 3.14 or newer. The verified release matrix is GIL-enabled CPython 3.14 on Linux, macOS, and Windows. Free-threaded CPython and other implementations are not supported; newer CPython versions are not yet verified.

## Quick start

Create a `test_*.py` file and decorate test functions with `@test(mark=...)`. Mark every test with the resources it may use:

```python
from snektest import assert_eq, test

@test(mark="fast")
def test_addition() -> None:
    assert_eq(1 + 1, 2)
```

Run tests with:

```bash
snektest
python -m snektest
```

Useful commands:

```bash
snektest --help
snektest --version
snektest --agent-docs
snektest --examples
snektest --example async
snektest examples
snektest example async
```

## Type checking is part of the contract

Run the strict `ty` type checker over test code before running tests. snektest does not generally re-validate at runtime what a type checker already rejects. One destructive misuse is checked during collection: applying `@test` without parentheses raises a clear error instead of silently removing the test. Other runtime validation focuses on what static checkers cannot see: CLI input, file paths, parameter case identifiers, and fixture protocol rules.

<!-- snektest-doc: expect-type-error=no-matching-overload@5, skip-run -->
```python
from snektest import test


# @test must be called; bare use is both a type error and a collection error.
@test
def test_needs_parentheses() -> None:
    pass
```

## Core patterns

- Import assertions from `snektest`; use the `assert_*` helpers, never bare
  `assert` (bare `assert` is banned in this repo's tests).
- Assertion argument order is intentional: pass the observed/computed value
  first and the expected/reference value second, following names like `actual`,
  `expected`, `member`, and `container`.
- `assert_is_not_none(x)` and `assert_isinstance(x, SomeType)` return the
  narrowed value; bind it to narrow for later use, or discard with `_ =` for a
  pure assertion.
- `assert_memory(peak_below=..., slope_below=..., rounds=..., warmup=...)` is a
  context-manager assertion for memory budgets (bytes as `int`). Wrap a region
  for a whole-block peak budget, or loop work over `m.rounds`. `rounds` must be
  1 through 1000, `warmup` must be non-negative, and a `slope_below` check needs
  at least 10 rounds. The maximum bounds the quadratic Theil-Sen fit. At least
  one budget is required — a budgetless call is a type error. `m.peak_bytes` /
  `m.growth_slope` stay readable after the block. Process-global tracing means
  measurements cannot nest or overlap sibling tasks/threads, and an async
  measured region must not `await` or yield to the event loop. Collection ends
  before the baseline. A borrowed tracemalloc session keeps the caller's depth,
  traces, peak history, and ownership; prior peaks are included conservatively.
- `assert_benchmark(median_below=..., p95_below=..., rounds=..., warmup=...)`
  asserts a sync or async region's typical and/or tail duration in seconds. At
  least one budget is required. Put setup before the context and loop timed work
  over `timing.rounds`. It reports min/median/p95/mean/stddev; GC is suspended
  during measured rounds unless `disable_gc=False`. Use `name=` to distinguish
  multiple timed regions in one test. Benchmark contexts cannot overlap because
  concurrent regions distort timings and process-wide GC state.
- Add `median_regression_below=0.10` and an optional
  `regression_noise_floor=` in seconds to opt a named region into a stored
  median comparison. Create snapshots with
  `--update-benchmark-baseline PATH`; enforce them with
  `--benchmark-baseline PATH`. Snapshots are machine-bound and reject hardware,
  OS, architecture, CPU-count, or Python-version mismatches. Use the same
  machine for both runs; on shared CI, generate the base-branch snapshot and
  compare the change in one job rather than committing hosted-runner timings.
  Missing current entries fail; stale unselected entries are ignored until an
  update covering their scope removes them. Updates are atomically replaced and
  locked so concurrent writers fail rather than lose changes.
- Mark every test. This is the recommended way to use snektest.
- Use `mark="fast"` for in-memory tests with no IO, threads, or subprocesses.
- Use `mark="medium"` for tests that use local IO or threads.
- Use `mark="slow"` for tests that use network IO, subprocesses, or expensive external resources.
- Async tests are regular `async def` functions decorated with `@test(mark=...)`. Function fixtures tear down before task-leak checks. A task created during fixture setup belongs to that fixture and stays alive through teardown; session-owned tasks may stay alive between tests. Snektest attributes abandoned fixture tasks to their fixture, cancels test-owned leaks, and leaves tasks from an embedding host application alone.
- Use `await asyncio.to_thread(...)` for blocking application work in an async test, and load fixtures before offloading. `to_thread` copies context; a raw `threading.Thread` does not inherit the fixture registry. An async timeout stops awaiting offloaded work but cannot stop its thread, which may continue or delay shutdown. Sync, CPU-bound, import, raw-thread, and sync-teardown hangs still need an outer process or CI timeout.
- Unhandled thread and `sys.unraisablehook` exceptions make an otherwise passing test an error. A new non-daemon thread still alive after function teardown makes it fail; joined threads pass, daemon threads and event-loop default-executor workers are exempt. Existing hooks are chained and restored. Extra background failures remain visible in console and JSON diagnostics. Snektest does not kill threads or provide a threaded test executor.
- Use `Param(value=..., name=...)` inside `@test([...], mark=...)` for parameterization. Every parameter list must be non-empty. Multiple lists form a Cartesian product capped at 10,000 cases per decorated test; larger products fail during collection. Case names must be unique and non-empty and cannot contain `, `, `[` or `]`, so their CLI filters are unambiguous. Static typing preserves parameter types through two lists; three or more use a variadic `Any` fallback. `@test_hypothesis` preserves strategy types through five strategies; six or more use its `Any` fallback.
- Define fixtures as generator functions decorated with `@fixture`, annotated `Generator[T]` or `AsyncGenerator[T]`. Load them with `load_fixture(fixture())` — call the decorated fixture and pass the returned handle.
- `@fixture` (default) is function-scoped. `@fixture(scope="session")` is cached once per execution process. `@fixture(scope="run")` is a zero-argument module-level fixture owned once by the command; it yields an inert stdlib-pickle descriptor of at most 1 MiB, and each worker receives an independent decoded copy. The literals and matching `Scope` enum members normalize to `Scope`; fixture handles expose that enum at runtime.
- Fixtures may take arguments; pass them at the call site, e.g. `load_fixture(make_user("Ada"))`. Calling a fixture twice gives two independent instances.
- Session and run fixtures must be zero-argument; use function fixtures for parameter-dependent setup, or return a factory/cache from a zero-argument cached fixture.
- A fixture may depend on another by calling `load_fixture()` in its body. Function may depend on function, session, or run; session on session or run; run on run only. An async fixture may depend on sync or async fixtures; a sync fixture cannot await an async dependency. A depending fixture is torn down before the fixtures it loaded.
- Put all `load_fixture(...)` calls at the beginning of the test, before actions or assertions.
- Avoid conditional or mid-test fixture loading unless delayed loading is the behavior under test.
- Collection and every selected module import finish before execution. Directory files are ordered by normalized path, cases retain source definition order, and filters retain command-line order. Absolute and relative paths share one canonical module identity, package-relative imports work, and imported decorated functions are not collected as local tests. Overlapping filters import a module once in one run; a later run imports it fresh. Captured import output and warnings stay out of test results and structured stdout. Local execution consumes the completed plan directly without a callback queue. `-n COUNT` / `--workers COUNT` uses persistent spawn workers plus one collector/run-fixture host; `auto` selects the lesser of process CPUs and selected cases. Each worker owns one event loop and its session fixtures. Results stay in manifest order, with active and completed-but-unreported cases capped at twice the worker count.
- `mutex="name"` on `@test` and `@test_hypothesis` prevents overlap for the same exact, non-empty, trimmed, case-sensitive command-local name. It is not an OS lock. Explicit workers cannot be combined with `--pdb`; `-s --json-output` is also rejected.
- Catch exported framework failures with `SnektestError`. `AssertionFailure`, `BadRequestError`, `CollectionError`, `FixtureError`, `SchemaGenerationError`, and `TestTimeoutError` share this ordinary `Exception` base; `AssertionFailure` also remains an `AssertionError`. Internal invariant errors are not exported.
- Console and JSON runs discard captured output from clean passing tests after reporting each result. Failure output is retained. `run_tests_programmatic` retains passing output by default. Normal runs retain bounded exception snapshots and clear ended traceback frames; `--pdb` keeps live frames until the first failure stops execution.
- Console summary lines are compact and may truncate exception details; use full failure details or `--json-output` when exact diagnostics matter.
- Filter runs with paths such as `snektest tests/test_math.py::test_addition` or markers such as `snektest --mark fast`.
- CLI runs bound every async test to 60 seconds by default. `snektest --timeout SECONDS` accepts only finite positive values; `snektest --no-timeout` disables the body limit. It is async-only and best-effort: it fires only while the test is suspended on an `await`. Async fixture teardown and task cancellation use the configured timeout, or a separate 60-second cleanup ceiling when the body is unbounded. Local collection and imports have no limit; explicit worker mode bounds child bootstrap but does not promise a per-import deadline. Sync bodies, CPU-bound work, raw threads, and sync teardown cannot be interrupted, even with workers. Hard limits need process-per-case isolation and separately isolated collection, which Snektest does not provide. Put an outer process or CI timeout around every command.
- Timeout interactions: for async `@test_hypothesis`, the timeout bounds the whole property run, not each example. If it fires while an example is suspended, snektest cancels the example and relays that outcome to the Hypothesis worker so the CLI exits promptly. Synchronous or CPU-bound work in that thread remains uninterruptible, and sync property tests are not bounded. Use Hypothesis's own `deadline`/`max_examples` for per-example limits and `--no-timeout` if the complete run must remain unbounded. With `--pdb`, a timed-out test post-mortems on snektest's internal timeout machinery, not the line that hung, so `--pdb` is of limited use for timeouts.
- Every requested filter must select a test by default; empty directories, files, marker selections, and individual filters fail with a collection error. Pass `--allow-empty` only when a zero-test selection is intentional. Explicit missing test-name and parameter-case filters remain errors even in allow-empty mode.
- Install `snektest[schema]` and use `@test_schema("openapi.json", base_url=..., mark="slow")` for positive OpenAPI contract tests. It collects one test per operation and checks for server errors and response-schema violations. Native Schemathesis auth providers and custom checks may be passed through `auth=` and `checks=`. Use `@test_schema_workflow` for linked stateful sequences. Decorated function bodies are declarative and are not called; `base_url` and `headers` may be fixture handles.
- Set `generation="negative"` on `@test_schema` to generate schema-violating requests. A passing response must use an allowed, documented 4xx status; `expected_statuses` defaults to all 4xx responses. Accepted 2xx responses and all 5xx responses fail. Negative stateful workflows are not supported.
- Recursive directory discovery excludes Git-ignored files; explicitly named test files still run. Outside a Git worktree, every matching `test_*.py` file is checked.

## Skips and known defects

Use `skip(reason)` when the current environment cannot run a test. Use
`xfail(reason)` for a known defect discovered during the test. For a known
assertion defect covering the whole test, use `@test(xfail="reason")`. Snektest
reports that assertion as XFAIL. If it passes, XPASS fails the command so stale
expected-failure declarations cannot hide fixes. Unexpected exceptions remain
errors. Reasons must be non-empty and already trimmed. Function fixtures still
tear down after either dynamic outcome, and teardown failures fail the command.

```python
import os

from snektest import assert_eq, skip, test


@test(mark="fast")
def test_optional_payment_service() -> None:
    if os.environ.get("PAYMENTS_URL") is None:
        skip("PAYMENTS_URL is not configured")


@test(mark="fast", xfail="comparison normalization is not fixed yet")
def test_known_comparison_defect() -> None:
    assert_eq("snektest", "SNEKTEST")
```

## Memory budgets

Assert peak allocation and leak-free growth with `assert_memory`:

```python
from snektest import assert_memory, test


@test(mark="fast")
def test_peak_budget() -> None:
    with assert_memory(peak_below=8 * 1024 * 1024):
        payload = bytearray(1024 * 1024)
        del payload


@test(mark="fast")
def test_no_leak() -> None:
    scratch: list[bytearray] = []
    with assert_memory(slope_below=64 * 1024, rounds=20) as m:
        for _ in m.rounds:
            scratch.clear()
            scratch.append(bytearray(32 * 1024))
    _ = m.peak_bytes
```

## OpenAPI contracts

The schema integration is an optional extra. A target URL may be supplied by a
session fixture so service setup remains under snektest's fixture lifecycle:

<!-- snektest-doc: skip-run -->
```python
from collections.abc import AsyncGenerator

from hypothesis import settings

from snektest import fixture, test_schema


@fixture(scope="session")
async def api_url() -> AsyncGenerator[str]:
    yield "http://127.0.0.1:8123"


@settings(max_examples=50, deadline=None)
@test_schema("openapi.json", base_url=api_url(), mark="slow")
async def test_api_contract() -> None:
    ...
```

Each operation is independently selectable, for example
`test_api_contract[GET /users/{user_id}]`. Custom checks run in addition to the
server-error and response-schema checks. Pass a native Schemathesis auth provider
class through `auth=` for login and refresh flows.

Use `@test_schema_workflow` to generate sequences from explicit OpenAPI links and
inferred producer-consumer relationships:

<!-- snektest-doc: skip-run -->
```python
from hypothesis import settings

from snektest import test_schema_workflow


@settings(max_examples=50, stateful_step_count=8, deadline=None)
@test_schema_workflow(
    "openapi.json",
    base_url="http://127.0.0.1:8123",
    mark="slow",
)
async def test_api_workflows() -> None:
    ...
```

A workflow is one Snektest result so Hypothesis can shrink the whole operation
sequence. It accepts the same auth, checks, fixtures, timeout, marker, and
Hypothesis settings as `@test_schema`. Linkless schemas and malformed links fail
during collection with guidance. A workflow failure includes its minimized
method/path/status sequence in console and JSON output without credentials,
query values, or request bodies.

Negative operation cases are named like
`test_invalid_requests[negative POST /users]`. Auth, checks, filters, fixtures,
response validation, and Hypothesis settings still apply. An operation with no
negatable request constraints reports an error and should be excluded with
`SchemaFilter`.

Filter operations before case or workflow generation with
`operations=SchemaFilter(...)`. A `SchemaOperationSelector` matches exact path,
method, tag, and operation-ID fields with AND semantics; selector tuples are OR
alternatives, excludes win, and `exclude_deprecated=True` removes deprecated
operations. Workflow filters must retain both ends of at least one link.

## Benchmarks

Assert async latency without starting another event loop:

```python
import asyncio

from snektest import assert_benchmark, test


@test(mark="fast")
async def test_async_checkpoint_latency() -> None:
    with assert_benchmark(
        name="async checkpoint",
        median_below=0.01,
        median_regression_below=0.10,
        regression_noise_floor=0.000001,
        rounds=20,
        warmup=3,
    ) as timing:
        for _ in timing.rounds:
            await asyncio.sleep(0)
```

Update or check the machine-bound snapshot:

```bash
snektest --update-benchmark-baseline .snektest-benchmarks.json tests/performance
snektest --benchmark-baseline .snektest-benchmarks.json tests/performance
```

## Copyable examples

List bundled examples:

```bash
snektest --examples
```

Print one example:

```bash
snektest --example basic
snektest --example fixtures
snektest --example async
snektest --example benchmark
snektest --example memory
snektest --example outcomes
snektest --example parametrize
snektest --example schema
```
"""

EXAMPLE_FILES: dict[str, str] = {
    "async": "async_tests.py",
    "basic": "basic_test.py",
    "benchmark": "benchmark.py",
    "fixtures": "fixtures.py",
    "memory": "memory.py",
    "outcomes": "outcomes.py",
    "parametrize": "parametrize.py",
    "schema": "schema.py",
}


def get_agent_docs() -> str:
    """Return the embedded guide for AI agents and humans."""
    return AGENT_DOCS


def get_examples_listing() -> str:
    """Return a human-readable list of bundled examples."""
    lines = [
        "Bundled snektest examples:",
        *[f"  {name:<12} snektest --example {name}" for name in sorted(EXAMPLE_FILES)],
    ]
    return "\n".join(lines) + "\n"


def get_example_source(example_name: str) -> str:
    """Return the source code for a bundled example."""
    normalized_name = example_name.removesuffix(".py")
    file_name = EXAMPLE_FILES.get(normalized_name)
    if file_name is None:
        file_name = next(
            (
                candidate
                for candidate in EXAMPLE_FILES.values()
                if candidate.removesuffix(".py") == normalized_name
            ),
            None,
        )
    if file_name is None:
        available = ", ".join(sorted(EXAMPLE_FILES))
        msg = f"Unknown example `{example_name}`. Use one of: {available}"
        raise BadRequestError(msg)

    resource = files("snektest.examples").joinpath(file_name)
    return resource.read_text(encoding="utf-8")
