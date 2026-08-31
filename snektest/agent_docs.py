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
- `@fixture` (default) is function-scoped: set up and torn down for each test. `@fixture(scope="session")` is set up once and reused across the run.
- Fixtures may take arguments; pass them at the call site, e.g. `load_fixture(make_user("Ada"))`. Calling a fixture twice gives two independent instances.
- Session fixtures must be zero-argument; use function fixtures for parameter-dependent setup, or return a factory/cache from a zero-argument session fixture.
- A fixture may depend on another by calling `load_fixture()` in its body. A function fixture may depend on function or session fixtures; a session fixture may depend on session fixtures but not function fixtures (raises `FixtureError`, since it would outlive the per-test dependency). An async fixture may depend on sync or async fixtures; a sync fixture cannot await an async dependency. A depending fixture is torn down before the fixtures it loaded, so it may use them during teardown.
- Put all `load_fixture(...)` calls at the beginning of the test, before actions or assertions.
- Avoid conditional or mid-test fixture loading unless delayed loading is the behavior under test.
- Tests run sequentially on a single shared event loop; avoid import-time side effects in test modules, and do not leave unawaited background tasks behind.
- Console summary lines are compact and may truncate exception details; use full failure details or `--json-output` when exact diagnostics matter.
- Filter runs with paths such as `snektest tests/test_math.py::test_addition` or markers such as `snektest --mark fast`.
- CLI runs bound every async test to 60 seconds by default. Override the limit with `snektest --timeout SECONDS` or disable it with `snektest --no-timeout`. It is async-only and best-effort: the timeout only fires while a test is suspended on an `await`, reporting a hung `await` as an error while the run continues; synchronous or CPU-bound work cannot be interrupted. There is no per-test timeout.
- Timeout interactions: for async `@test_hypothesis`, the timeout bounds the whole property run (not each example) and the Hypothesis worker thread keeps running after it fires, so use Hypothesis's own `deadline`/`max_examples` for per-example limits and `--no-timeout` if the complete run must remain unbounded; sync property tests are not bounded. With `--pdb`, a timed-out test post-mortems on snektest's internal timeout machinery, not the line that hung, so `--pdb` is of limited use for timeouts.
- Explicit test-name and parameter-case filters fail if the requested test or case is not found.
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
```
"""

EXAMPLE_FILES: dict[str, str] = {
    "async": "async_tests.py",
    "basic": "basic_test.py",
    "benchmark": "benchmark.py",
    "fixtures": "fixtures.py",
    "memory": "memory.py",
    "parametrize": "parametrize.py",
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
