"""Embedded documentation and examples for coding agents."""

from importlib.resources import files

from snektest.models import BadRequestError

AGENT_DOCS = """# snektest agent guide

Snektest is a Python testing framework with first-class async and typing support.

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
snektest --agent-docs
snektest --examples
snektest --example async
snektest examples
snektest example async
```

## Type checking is part of the contract

Run the strict `ty` type checker over test code before running tests. snektest does not re-validate at runtime what a type checker already rejects, so unchecked misuse — such as applying `@test` without parentheses — can fail silently. Runtime validation is reserved for what static checkers cannot see: CLI input, file paths, and fixture protocol rules.

<!-- snektest-doc: expect-type-error=no-matching-overload@5, skip-run -->
```python
from snektest import test


# @test must be *called*: applying it bare is a type error, not a runtime one.
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
  for a whole-block peak budget, or loop work over `m.rounds` (needs
  `rounds >= 10` for a `slope_below` leak check). At least one budget is
  required — a budgetless call is a type error. `m.peak_bytes` /
  `m.growth_slope` stay readable after the block. Cannot be nested.
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
- Async tests are regular `async def` functions decorated with `@test(mark=...)`. An async test fails if it leaves a task it created pending; snektest cancels and awaits leaked tasks to isolate later tests.
- Use `Param(value=..., name=...)` inside `@test([...], mark=...)` for parameterization.
- Define fixtures as generator functions decorated with `@fixture`, annotated `Generator[T]` or `AsyncGenerator[T]`. Load them with `load_fixture(fixture())` — call the decorated fixture and pass the returned handle.
- `@fixture` (default) is function-scoped. `@fixture(scope="session")` is cached once per execution process. `@fixture(scope="run")` is a zero-argument module-level fixture owned once by the command; it yields an inert stdlib-pickle descriptor of at most 1 MiB, and each worker receives an independent decoded copy.
- Fixtures may take arguments; pass them at the call site, e.g. `load_fixture(make_user("Ada"))`. Calling a fixture twice gives two independent instances.
- Session and run fixtures must be zero-argument; use function fixtures for parameter-dependent setup, or return a factory/cache from a zero-argument cached fixture.
- A fixture may depend on another by calling `load_fixture()` in its body. Function may depend on function, session, or run; session on session or run; run on run only. An async fixture may depend on sync or async fixtures; a sync fixture cannot await an async dependency. A depending fixture is torn down before the fixtures it loaded.
- Put all `load_fixture(...)` calls at the beginning of the test, before actions or assertions.
- Avoid conditional or mid-test fixture loading unless delayed loading is the behavior under test.
- Collection finishes before execution. Omit workers for local sequential execution. `-n COUNT` / `--workers COUNT` uses persistent spawn workers plus one collector/run-fixture host; `auto` selects the lesser of process CPUs and selected cases. Each worker owns one event loop and its session fixtures. Results stay in manifest order.
- `mutex="name"` on `@test` and `@test_hypothesis` prevents overlap for the same exact, non-empty, trimmed, case-sensitive command-local name. It is not an OS lock. Explicit workers cannot be combined with `--pdb`; `-s --json-output` is also rejected.
- Console summary lines are compact and may truncate exception details; use full failure details or `--json-output` when exact diagnostics matter.
- Filter runs with paths such as `snektest tests/test_math.py::test_addition` or markers such as `snektest --mark fast`.
- CLI runs bound every async test to 60 seconds by default. Override the limit with `snektest --timeout SECONDS` or disable it with `snektest --no-timeout`. It is async-only and best-effort: the timeout only fires while a test is suspended on an `await`, reporting a hung `await` as an error while the run continues; synchronous or CPU-bound work cannot be interrupted. There is no per-test timeout.
- Timeout interactions: for async `@test_hypothesis`, the timeout bounds the whole property run (not each example) and the Hypothesis worker thread keeps running after it fires, so use Hypothesis's own `deadline`/`max_examples` for per-example limits and `--no-timeout` if the complete run must remain unbounded; sync property tests are not bounded. With `--pdb`, a timed-out test post-mortems on snektest's internal timeout machinery, not the line that hung, so `--pdb` is of limited use for timeouts.
- Explicit test-name and parameter-case filters fail if the requested test or case is not found.
- Install `snektest[schema]` and use `@test_schema("openapi.json", base_url=..., mark="slow")` for positive OpenAPI contract tests. It collects one test per operation and checks for server errors and response-schema violations. Native Schemathesis auth providers and custom checks may be passed through `auth=` and `checks=`. Use `@test_schema_workflow` for linked stateful sequences. Decorated function bodies are declarative and are not called; `base_url` and `headers` may be fixture handles.
- Set `generation="negative"` on `@test_schema` to generate schema-violating requests. A passing response must use an allowed, documented 4xx status; `expected_statuses` defaults to all 4xx responses. Accepted 2xx responses and all 5xx responses fail. Negative stateful workflows are not supported.
- Recursive directory discovery excludes Git-ignored files; explicitly named test files still run. Outside a Git worktree, every matching `test_*.py` file is checked.

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
