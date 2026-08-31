# AGENTS.md

## Project Overview

snektest is a Python testing framework with first class support for async and static typing.

## Development Commands

### Guidelines for writing tests
- Do not use monkeypatching or mocking.
- Never use bare `assert` in tests; use the `assert_*` helpers from
  `snektest.assertions`. This is enforced by ruff (`S101` is not ignored for
  `tests/*`); a genuinely unavoidable bare assert needs an explicit
  `# noqa: S101`.

### Running Tests
```bash
# Run all tests
uv run snektest

# Run a specific test file
uv run snektest tests/test_myfeature.py

# Run a specific test function
uv run snektest tests/test_myfeature.py::test_something

# Run a specific parameterized case (case name in brackets)
uv run snektest tests/test_myfeature.py::test_something[case-name]

# Run one marker group
uv run snektest --mark fast
```

Explicit test-name and parameter-case filters are expected to error when the
requested test or case does not exist.

### Type Checking & Linting
```bash
# Type check
uv run ty check

# Lint/format
uv run ruff check
uv run ruff format --check
```

## Fixing linting issues
If possible let `ruff` fix issues automatically.

```bash
# Apply all automated fixes and format
uv run ruff check --fix .
uv run ruff format .
```

### Package Management
This project uses `uv` for dependency management. The project requires Python >=3.14.

## Architecture

### Test Collection & Execution Flow

1. **CLI Entry** (`cli.py:main`): Parse args, create filter items, start async event loop
2. **Producer-Consumer Pattern**:
   - Producer thread (`load_tests_from_filters`) walks the filesystem, excludes Git-ignored candidates for directory filters, imports test modules, and adds tests to the async queue. Explicit file filters bypass Git ignore checks.
   - Consumer coroutine (`run_tests`) awaits tests from the queue one at a time, on a single event loop, while collection continues in the producer thread
3. **Test Discovery** (`load_tests_from_file`): Import modules, find functions decorated with `@test()`, expand parameterized tests
4. **Test Execution** (`execute_test`): Capture stdout/stderr, execute test function (sync or async), teardown function fixtures, return `TestResult`

### Fixture System

Fixtures are generator functions decorated with `@fixture`, annotated
`Generator[T]` or `AsyncGenerator[T]`. Calling a decorated fixture returns a
handle (`Fixture[T]` / `AsyncFixture[T]`, defined in `annotations.py`); pass it to
`load_fixture()`. The handle carries the fixture's `scope`, a `key` (the
decorated function), and a `make` callable, so scope is read directly off the
decorator — no frame/annotation inspection. The handle is also a (async) context
manager, so fixtures double as setup helpers in standalone scripts.

All fixture state and teardown is owned by a `FixtureRegistry` (`fixtures.py`),
created fresh per run and reached ambiently through a `ContextVar` (set by
`run_tests` via `use_registry`). `load_fixture` is a free function that reads the
current registry — tests take no context parameter.

- **Function fixtures** (`@fixture`): Set up on each `load_fixture()` call,
  pushed onto the registry's function stack, torn down after each test in reverse
  (first-in-last-out) order. May take arguments, passed at the call site.
- **Session fixtures** (`@fixture(scope="session")`): Cached in the registry
  keyed by the decorated function, created on first `load_fixture()` call, reused
  across tests, torn down after all tests complete. Concurrent first-awaits of an
  async session fixture share one setup coroutine. Session fixtures must not
  accept parameters (enforced statically via the `@fixture(scope="session")`
  overload and at load time by the registry); use function fixtures for
  parameter-dependent setup, or return a factory/cache from a zero-argument
  session fixture.
- **Fixtures depending on fixtures**: a fixture may `load_fixture()` another in
  its body (resolved through the ambient registry). The dependency is registered
  for teardown only after its own setup completes, so it lands below the
  depending fixture on the teardown stack and is torn down *after* it — a
  depending fixture may use its dependency during teardown. This holds for both
  scopes. A function fixture may depend on function or session fixtures; a
  session fixture may depend on session fixtures only — depending on a function
  fixture raises `FixtureError` at load time, because the cached session fixture
  would outlive the per-test dependency. An async fixture may depend on sync or
  async fixtures; a sync fixture cannot await an async dependency.

```python
from collections.abc import AsyncGenerator

from snektest import fixture

@fixture
async def my_fixture() -> AsyncGenerator[str]:
    # setup
    yield "value"
    # teardown
```

### Markers

`@test(mark=...)` attaches a built-in marker describing the resources a test may
use: `"fast"` (in-memory, no IO/threads/subprocesses), `"medium"` (local IO or
threads), or `"slow"` (network IO, subprocesses, or other expensive external
resources). Marking every test is the recommended public style; filter a run to
one group with `--mark fast|medium|slow`. `Marker` (`decorators.py`) is the type
alias for the three literals; markers are passed as a single literal.

### Async Hygiene

After a successful async test body, `execute_test` compares event-loop task
snapshots. Newly created tasks still pending are cancelled and awaited, and the
test becomes a failure. Pending tasks are also cancelled when the body fails or
errors, but the original result is preserved. Sync tests are not checked.

### Timeouts

CLI runs apply a 60-second timeout to every async test by default.
`--timeout SECONDS` overrides it, while `--no-timeout` disables it. The CLI
passes that run-wide ceiling to `execute_test`, which wraps the awaited test body
in `asyncio.timeout`. It is async-only and best-effort: the timeout only fires
while the test is suspended on an `await`, so a hung `await` becomes an error
(`TestTimeoutError`, reported as ERROR) and the run continues, while synchronous
or CPU-bound work cannot be interrupted. A `TimeoutError` the test raised itself
is distinguished from a fired timeout via `Timeout.expired()` and passes through
unchanged. Timed-out tests still run function-fixture teardown. There is no
per-test timeout. Direct programmatic runner calls remain unbounded unless they
pass `timeout`.

Interactions:

- **`@test_hypothesis`.** An async property test runs every example inside a
  single `await asyncio.to_thread(run_hypothesis)` (`decorators.py`), so the
  timeout wraps the whole property run, not each example. When it fires,
  `asyncio.timeout` cancels the `await` but the worker thread keeps running to
  completion — threads aren't cancellable — so a runaway property test is reported
  as timed out while still burning CPU in the background. Sync property tests are
  not coroutines, so the timeout never applies. Prefer Hypothesis's own
  `deadline`/`max_examples` for per-example bounds; use `--no-timeout` if the
  complete property run must remain unbounded.
- **`--pdb`.** `TestTimeoutError` flows through the normal error path, so
  `_maybe_debug_test_result` will post-mortem on it. The cancellation unwinds the
  test's own `await` frame before `TestTimeoutError` is raised (with `from None`)
  inside `_await_test_body`, so `_traceback_for_file` finds no test-file frame and
  the debugger opens on snektest's internal timeout machinery rather than the hang
  site. It works (post-mortem runs after the test returns; no deadlock) but is of
  limited use for locating a timeout.

### Parameterization

Tests can accept multiple parameter sets via `@test([...], [...], mark=...)`,
each list built from `Param(value=..., name=...)`. `Param.to_dict()` creates all
combinations using `itertools.product`, keyed by param names. Each combination
becomes a separate test execution.

### Summary Output

Console summary lines intentionally keep exception details compact: only the first
exception message line is shown and long lines may be ellipsized. Use the full
failure details or `--json-output` for exact diagnostics.

### Assertions

Custom assertion system with rich error reporting. Use the `assert_*` helpers from
`snektest.assertions` rather than bare `assert` (bare `assert` is banned in tests;
see "Guidelines for writing tests"). Assertion helper argument order is
intentional: pass the observed/computed value first and the expected/reference
value second, following parameter names like `actual`, `expected`, `member`, and
`container`. Raises `AssertionFailure` with actual/expected values for better
error messages.

The narrowing helpers return the narrowed value so a single call both asserts and
narrows under the strict ty config: `assert_is_not_none(x)` returns `x`
typed as non-`None`, and `assert_isinstance(obj, SomeType)` returns `obj` typed as
`SomeType`. Bind the result (`opts = assert_isinstance(result, CliOptions)`) to
narrow for later attribute access; for a pure assertion discard it
(`_ = assert_isinstance(x, int)`) to make that intent explicit.

### Memory Assertions

`assert_memory` (`assertions.py`) is a context-manager assertion, in the
`assert_raises` family, for peak-allocation budgets and leak detection. Budgets
are bytes-as-`int`; overloads make a budgetless call a type error. It measures
through a pluggable `MemoryBackend` (`memory.py`) — a thread-inclusive seam that
does not assume a synchronous `int` return, so a future memray backend slots in
behind the same protocol. Only `TracemallocBackend` exists today; it baselines
out tracemalloc's own allocations and never stops tracing it did not start.

- Whole-block mode (`rounds=1`, `m.rounds` untouched) takes one peak sample over
  the block; there is no warmup in this mode.
- Rounds mode loops work over `m.rounds` (a stateful iterator of
  `warmup + rounds` iterations). `peak_bytes` is the max single-round peak;
  `growth_slope` is a Theil–Sen fit of retained bytes per round (bytes/round).
  A `slope_below` budget requires `rounds >= 10` (`BadRequestError` otherwise).
- Guarded misuse (all `BadRequestError`): nesting, a `slope_below` budget under
  ten rounds, and a rounds iterator left unconsumed or partially consumed.
- Passing measurements flow through a run-scoped contextvar sink
  (`memory.py`) into `PassedResult.measurements`, are rendered on the green
  result line by the presenter, and appear under `memory_measurements` in
  `--json-output`.

### Type Checking Configuration

The project uses ty with every available rule set to `error`, plus strict equality
semantics and strict generic narrowing. No rules are disabled. When adding code,
expect to fully annotate it and run `uv run ty check`.

## Documentation Surfaces

User-facing guidance lives in four places that must stay in sync. When changing
public behavior or recommendations, update all of them in the same change:

1. `README.md` — user docs
2. `snektest/agent_docs.py` (`AGENT_DOCS`) — embedded guide printed by `--agent-docs`
3. `snektest/examples/*.py` — bundled examples printed by `--example <name>`
4. This file (`AGENTS.md`) — contributor/architecture docs

Rules of thumb:
- The canonical import style in all examples is top-level: `from snektest import assert_eq, test`.
- Never hand-write sample test output in `README.md`; run the example with `uv run snektest` and paste the actual output.
- Code blocks in docs must type-check under this repo's strict ty config and run as written.
- These rules are enforced by `tests/meta/test_doc_blocks.py`, which extracts every ```python block from `README.md` and `AGENT_DOCS`, type-checks them with ty, runs them with snektest, and diffs each adjacent ```text block against captured output. Annotate exceptions with an HTML comment directive before the fence, e.g. `<!-- snektest-doc: expect-fail -->` or `<!-- snektest-doc: expect-type-error, skip-run -->`. `expect-type-error` optionally pins a specific diagnostic — `expect-type-error=invalid-argument-type` (that rule anywhere) or `expect-type-error=invalid-argument-type@10` (that rule at block line 10) — so a signature regression to a different rule or line still fails the test (see `testutils/docblocks.py`).

### Code Style Notes

- Ruff with extensive rules enabled (see pyproject.toml:22-64)
- Tests allow magic numbers, assert statements, private access (see per-file-ignores)
- Line length 88, but E501 ignored (long strings for messages okay)
- Mixed case names allowed in `annotations.py` for validators like `validate_SomeType`
