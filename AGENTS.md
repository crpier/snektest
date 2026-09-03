# AGENTS.md

## Project Overview

snektest is a Python testing framework with first class support for async and static typing.

## Development Commands

### Complete release gate

```bash
uv run --locked python scripts/release_check.py
```

This is the one release-health command locally and in GitHub Actions. It checks
the lock, Ruff formatting and lint, ty, production dependency advisories, the
complete canonical `tests/` suite under coverage, the 90% core-package threshold,
and independently built and installed wheel and source archives. CI runs it on
Python 3.14 under Linux, macOS, and Windows with a 15-minute outer timeout.

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

Every filter must select at least one test by default. `--allow-empty` permits
empty files, directories, marker selections, and filters only when intentional;
explicit missing test-name and parameter-case filters remain errors.

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
This project uses `uv` for dependency management. The project requires Python >=3.14. The verified support matrix is GIL-enabled CPython 3.14 on Linux, macOS, and Windows. Free-threaded CPython and other implementations are unsupported; newer CPython versions are not yet verified.

### Distribution Metadata

`snektest/_version.py` is the authoritative release version; the package exports
it as `snektest.__version__`, and the CLI prints it with `--version`. Development
builds use a PEP 440 development version and must never reuse a published
identity. Hatch's sdist target is an explicit allowlist. Both wheel and sdist
must include the package, typing marker, bundled examples, README, and MIT
license metadata, while excluding contributor-only and workstation files.
Artifact tests inspect both manifests and independently install and smoke-test
each archive. Versioned `v*` tags are immutable, must point to a pull request
merged into `main`, and must exactly match the package version. The release
workflow reruns the matrix gate before PyPI trusted publishing with attestations.
User-facing changes belong in `CHANGELOG.md`; private security reports follow
`SECURITY.md`.

## Architecture

### Test Collection & Execution Flow

1. **CLI Entry** (`cli.py:main`): Parse args, create filter items, start async event loop
2. **Canonical Collection** (`collect_tests_from_filters`): Resolve one canonical path identity for absolute or relative filters, support package-relative imports through an isolated synthetic namespace, import each module once per run, sort directory candidates by normalized path, preserve source and filter order, finish all imports before execution, reject empty filters/plans unless explicitly allowed, reject invalid or colliding parameter cases, and assign an ordinal to every occurrence. Later in-process runs import modules fresh; failed imports roll back. Imported decorated functions are excluded by module ownership. Captured import output and warnings never enter test results or structured stdout. Explicit files bypass Git ignore checks; repeated filters remain repeated cases. Bare `@test` raises during collection rather than disappearing.
3. **Local Execution**: With workers omitted, publish the complete plan to `run_tests`, which executes sequentially in process on one event loop.
4. **Process Execution** (`parallel.py`): Explicit workers use spawn. One persistent host owns canonical collection and run fixtures; N persistent execution workers independently recollect and validate the manifest, each owning an event loop and session registry. The coordinator schedules mutex-compatible ordinals and reports in manifest order.
5. **Test Execution** (`execute_test`): Capture stdout/stderr, execute one sync or async test, teardown function fixtures, and return a process-neutral `TestResult`.

### Fixture System

Fixtures are generator functions decorated with `@fixture`, annotated
`Generator[T]` or `AsyncGenerator[T]`. Calling a decorated fixture returns a
handle (`Fixture[T]` / `AsyncFixture[T]`, defined in `annotations.py`); pass it to
`load_fixture()`. The handle carries its canonical `Scope` enum value as `scope`,
a `key` (the decorated function), and a `make` callable, so scope is read
directly off the decorator — no frame/annotation inspection. The handle is also
a sync or async context manager, so fixtures double as setup helpers in
standalone scripts.

All fixture state and teardown is owned by a `FixtureRegistry` (`fixtures.py`),
created fresh per run and reached ambiently through a `ContextVar` (set by
`run_tests` via `use_registry`). `load_fixture` is a free function that reads the
current registry — tests take no context parameter.

- **Function fixtures** (`@fixture`): Set up on each `load_fixture()` call,
  pushed onto the registry's function stack, torn down after each test in reverse
  (first-in-last-out) order. May take arguments, passed at the call site.
- **Session fixtures** (`@fixture(scope="session")`): Cached in one execution-process registry
  keyed by the decorated function, created on first `load_fixture()` call, reused
  across tests assigned to that process, torn down when it exits. Concurrent first-awaits of an
  async session fixture share one setup coroutine. Session fixtures must not
  accept parameters (enforced statically via the `@fixture(scope="session")`
  overload and at load time by the registry); use function fixtures for
  parameter-dependent setup, or return a factory/cache from a zero-argument
  session fixture. Worker replacement creates a new session incarnation.
- **Run fixtures** (`@fixture(scope="run")`): Zero-argument module-level fixtures
  set up lazily and exactly once by the fixture host. The yielded descriptor must
  round-trip through stdlib pickle in at most 1 MiB. Publication stages an
  independent decoded copy in every worker before commit; the host retains the
  original for teardown after worker sessions. Local mode enforces the same
  descriptor-copy contract.
- **Fixtures depending on fixtures**: a fixture may `load_fixture()` another in
  its body (resolved through the ambient registry). The dependency is registered
  for teardown only after its own setup completes, so it lands below the
  depending fixture on the teardown stack and is torn down *after* it — a
  depending fixture may use its dependency during teardown. This holds for both
  scopes. Function may depend on function, session, or run; session on session or
  run; run on run only. A session fixture depending on a function
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

`mutex="name"` on either test decorator declares one exact command-local mutex.
Same-name selected cases do not overlap; blocked ordinals are skipped so unrelated
cases can run. Mutexes are case-sensitive and do not provide cross-command or OS
locking.

### Intentional outcomes

`skip(reason)` and `xfail(reason)` stop a sync or async test dynamically. Reasons
must be non-empty, already-trimmed strings. `@test(xfail="reason")` marks a known
Snektest assertion failure as expected; unrelated exceptions remain errors. A
passing statically expected failure is XPASS and makes the command exit 1. SKIP
and XFAIL alone exit 0. All are first-class `TestResult` variants with matching
programmatic counts and JSON statuses. Function fixtures established before a
dynamic outcome still tear down. Teardown and background failures override the
successful exit behavior, and abandoned async tasks turn the outcome into a
failure.

### Process Workers

`-n COUNT` / `--workers COUNT` accepts a positive integer or `auto`; omission is
local sequential mode. `auto` resolves to
`min(os.process_cpu_count() or 1, selected_count)`. The count covers execution
workers only; process mode also has one fixture host, and `--workers 1` still
uses children. `--pdb` with workers and `-s --json-output` are usage errors.

### Async Hygiene

`execute_test` tags child tasks by execution context instead of comparing global
event-loop snapshots. Function fixtures tear down before test-owned tasks are
classified. Tasks created during fixture setup inherit that fixture's owner and
remain alive through its teardown; session-owned tasks may survive between tests.
A fixture that returns while its tasks remain pending receives an attributed
teardown failure. Test-owned leaks are cancelled after function teardown. New
tasks from an unrelated embedding application have no test owner and are left
alone. Cancellation waits are bounded; a resistant coroutine is force-closed and
the owning test or fixture fails.

### Thread Observability

Execution temporarily installs and chains `threading.excepthook` and
`sys.unraisablehook` around each canonically sequential test, including function
teardown and task cleanup. An unhandled thread or unraisable exception turns a
pass into an error. New non-daemon threads alive afterward turn a pass into a
failure; daemon and current event-loop default-executor threads are exempt.
Additional background failures on an existing failure/error remain process-safe
`BackgroundFailure` diagnostics in console and JSON. Hooks are process-global,
so concurrent direct `execute_test` calls are unsupported. The standard runner
never overlaps tests within one process.

The supported blocking pattern is `await asyncio.to_thread(...)`. Context is
copied there, but fixtures should still be loaded before offload; raw
`threading.Thread` does not inherit the fixture-registry context. Threads cannot
be cancelled. A timed-out `to_thread` await leaves its function running and it
may delay event-loop shutdown. Raw leaked threads, sync or CPU-bound hangs,
imports, and sync teardown still need an outer process/CI timeout. There is no
threaded test executor.

### Timeouts

CLI runs apply a 60-second timeout to every async test by default.
`--timeout SECONDS` accepts a finite positive value, while `--no-timeout`
disables the test-body limit. The CLI passes that run-wide ceiling to
`execute_test`, which wraps the awaited test body in `asyncio.timeout`. It is
async-only and best-effort: the timeout only fires while the test is suspended
on an `await`, so a hung `await` becomes an error (`TestTimeoutError`, reported
as ERROR) and the run continues. Synchronous or CPU-bound work cannot be
interrupted. A `TimeoutError` the test raised itself is distinguished from a
fired timeout via `Timeout.expired()` and passes through unchanged. There is no
per-test timeout. Direct programmatic runner calls leave test bodies unbounded
unless they pass `timeout`.

Local collection and imports have no Snektest timeout. In explicit worker mode,
the configured timeout bounds each child bootstrap, but it is not a per-import
guarantee. Persistent workers also cannot enforce hard sync-body deadlines.
Hard sync and import limits require process-per-case isolation plus separately
isolated collection. Snektest deliberately leaves those hard limits to an outer
process or CI timeout; its release jobs use 15 minutes.

Cleanup remains bounded when the body timeout is disabled. Each async fixture
teardown and task-cancellation attempt gets the configured timeout, or 60 seconds
when `timeout=None`. Cleanup failures identify the fixture and do not stop later
teardown attempts. Function teardown runs before task-leak classification.
`SystemExit`, `KeyboardInterrupt`, and parent task cancellation propagate only
after cleanup; explicit test-raised `CancelledError` remains a failed test.
Synchronous teardown cannot be interrupted on the local event-loop thread and
requires an outer process or CI timeout.

Interactions:

- **`@test_hypothesis`.** An async property test runs every example inside a
  single `await asyncio.to_thread(run_hypothesis)` (`decorators.py`), so the
  timeout wraps the whole property run, not each example. If it fires while an
  example is suspended, task cancellation completes the cross-thread handoff and
  lets the Hypothesis worker stop. Synchronous or CPU-bound work in that thread
  remains uninterruptible. Sync property tests are not coroutines, so the timeout
  never applies. Prefer Hypothesis's own `deadline`/`max_examples` for per-example
  bounds; use `--no-timeout` if the complete property run must remain unbounded.
- **`--pdb`.** `TestTimeoutError` flows through the normal error path, so
  `_maybe_debug_test_result` will post-mortem on it. The cancellation unwinds the
  test's own `await` frame before `TestTimeoutError` is raised (with `from None`)
  inside `_await_test_body`, so `_traceback_for_file` finds no test-file frame and
  the debugger opens on snektest's internal timeout machinery rather than the hang
  site. It works (post-mortem runs after the test returns; no deadlock) but is of
  limited use for locating a timeout.

### Parameterization

Tests can accept multiple parameter sets via `@test([...], [...], mark=...)`,
each non-empty list built from `Param(value=..., name=...)`. Static checking
preserves parameter types through two lists; three or more use the variadic
`Any` fallback. Case names are
non-empty, unique, and exclude `, `, `[` and `]` so CLI filters are unambiguous.
`Param.to_dict()` creates all combinations using `itertools.product`; each
combination becomes a separate test execution and cardinality is the product of
axis sizes.

### OpenAPI Contract Testing

`test_schema` (`schema.py`) is an optional Schemathesis integration installed
with `snektest[schema]`. It loads a local OpenAPI JSON/YAML document during
collection and expands it into one ordinary parameterized Snektest case per
operation. Each operation runs a positive Schemathesis strategy through the
existing Hypothesis worker-thread path, using Schemathesis's synchronous HTTP
transport so the main event loop remains available to fixture-started services.

The integration always runs `not_a_server_error` and
`response_schema_conformance`; native Schemathesis checks passed through
`checks=` run in addition. A native `AuthProvider` class passed through `auth=`
supports cached login and token-refresh flows. Schemathesis raises contract
failures as a `BaseExceptionGroup`, so the adapter translates them to
`AssertionFailure` without changing core result classification. Network and
configuration exceptions remain errors. Literal targets and fixture-backed
`base_url` / `headers` values are resolved before the worker starts. The
decorated function is metadata-only and is not called.

`test_schema_workflow` builds Schemathesis's Hypothesis state machine from
explicit OpenAPI links and inferred producer-consumer relationships. It reports
one Snektest result for the complete generated workflow so Hypothesis can shrink
the operation sequence as a unit. It shares the operation decorator's auth,
checks, fixtures, timeout, marker, and Hypothesis-settings behavior. Construction
rejects schemas with no usable transitions and translates Schemathesis's grouped
link-validation errors to `BadRequestError` with source/status/link/target
context. Schemathesis clears Hypothesis's state-machine notes, so the adapter
records the final minimized method/path/status sequence itself and appends it to
the `AssertionFailure`; credentials, query values, and bodies are intentionally
excluded so console and JSON diagnostics are safe to retain.

Both schema decorators accept a `SchemaFilter`, applied to the loaded schema
before operation collection or state-machine construction. Each
`SchemaOperationSelector` combines its exact path/method/tag/operation-ID fields
with AND semantics; include and exclude tuples are OR sets, excludes take
precedence, and deprecated operations may be excluded globally. Operation tests
collect through Schemathesis's filter-aware `get_all_operations()` result
iterator rather than its unfiltered mapping interface. Empty operation selections
and workflow selections that retain no complete producer-consumer link raise
`BadRequestError` during collection.

`test_schema(generation="negative")` passes Schemathesis's negative generation
mode into each selected operation strategy. It enables
`negative_data_rejection` and `status_code_conformance` in addition to the
always-on server-error and response-schema checks. `expected_statuses` defaults
to all 4xx statuses and may narrow that non-empty set; 2xx acceptance,
undocumented statuses, configured-status mismatches, and all 5xx responses are
failures. Negative case names are prefixed with `negative`. Hypothesis
`Unsatisfiable` from an operation with no negatable request constraints is
translated to `SchemaGenerationError` with filtering guidance. Stateful
workflows remain positive-only.

### Summary Output

Console summary lines intentionally keep exception details compact: only the first
exception message line is shown and long lines may be ellipsized. Use the full
failure details or `--json-output` for exact diagnostics.

### Public Interface and Errors

The runtime and `snektest/__init__.pyi` expose the same deliberate top-level
interface. `Scope` is the canonical runtime/static fixture-scope representation;
accepted string literals normalize to it. `SnektestError` is the conventional
`Exception`-based root for exported assertion, request, collection, fixture,
schema-generation, and timeout errors. `AssertionFailure` also subclasses
`AssertionError`. `UnreachableError` remains internal `BaseException` control
flow so ordinary test-error handlers do not misclassify framework invariants.
`@test` statically preserves up to two parameter axes and `@test_hypothesis` up
to five strategies; larger
arities use documented variadic `Any` fallbacks. Distribution tests validate the
built wheel rather than only the checkout.

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
A process-global non-blocking owner guard rejects overlap across sibling tasks or
threads; the contextvar separately provides same-context nesting diagnostics.
An event-loop probe rejects an async region that suspends, because unrelated
sibling allocations could contaminate the global trace. Borrowed tracing
preserves the caller's depth, live traces, peak history, and
ownership. Because stdlib cannot restore a historical peak after reset, borrowed
measurements do not reset it and conservatively include prior peak history.

- Whole-block mode (`rounds=1`, `m.rounds` untouched) takes one peak sample over
  the block; there is no warmup in this mode.
- Rounds mode loops work over `m.rounds` (a stateful iterator of
  `warmup + rounds` iterations). `peak_bytes` is the max single-round peak;
  `growth_slope` is a Theil–Sen fit of retained bytes per round (bytes/round).
  `rounds` must be 1 through 1000 and `warmup >= 0`; the upper bound caps the
  fit's quadratic pairwise-slope allocation. A `slope_below` budget requires
  `rounds >= 10` (`BadRequestError` otherwise).
- Guarded misuse (all `BadRequestError`): nesting or process-wide overlap,
  invalid round/warmup counts, a `slope_below` budget under ten rounds, and a
  rounds iterator left unconsumed or partially consumed.
- Passing measurements flow through a run-scoped contextvar sink
  (`memory.py`) into `PassedResult.measurements`, are rendered on the green
  result line by the presenter, and appear under `memory_measurements` in
  `--json-output`.

### Performance Benchmarks

`assert_benchmark(median_below=..., p95_below=..., rounds=100, warmup=10)` is a
context-manager assertion for sync or async timing budgets in seconds. At least
one of the median or p95 budgets is required. Setup goes before the context;
timed work loops over `timing.rounds`, a stateful iterator of warmup plus
measured iterations. It reports min, median, nearest-rank p95, mean, and
population standard deviation, and uses strict `<` for each configured budget.
GC is suspended only during measured rounds by default. Optional `name=` values
identify multiple timed regions in console and JSON output. Benchmark contexts
cannot overlap because concurrent regions distort timings and process-wide GC
state.

`median_regression_below=` opts a named region into a stored median comparison;
the value is a fractional increase, and `regression_noise_floor=` supplies an
absolute allowance in seconds. The effective allowance is the larger of the
baseline-relative allowance and the noise floor, and the observed median must
remain strictly below baseline plus that allowance. Existing absolute budgets
still apply first. Complete benchmark measurements are retained on pass, failure,
and error results so baseline diagnostics remain available in console and JSON.

`--update-benchmark-baseline PATH` atomically updates opted-in regions after a
fully successful run. Filtered runs replace only matching entries and preserve
unselected entries; marked runs replace only observed tests. Baseline identity is
the project-relative path, test function, parameter case, and required unique
region name. Rounds, warmup, and GC policy are part of the stored measurement
protocol. `--benchmark-baseline PATH` compares at context exit, before result
reporting, so regressions are normal test failures with useful source tracebacks.
Comparison ignores stale entries outside the observed run; a matching update
prunes them. A rename is a missing current identity until an update covers both
the old and new scope.
Updates hold a sidecar lock across the read/merge/write cycle and use atomic
replacement. A concurrent writer fails; a killed writer can leave a stale lock
that must be removed explicitly.

Snapshots store an exact machine fingerprint: Python implementation/version,
operating system, architecture, CPU model, and logical CPU count. Load and update
reject mismatches. No normalization is attempted. Shared hosted CI should create
a base-branch snapshot and compare the candidate on the same job machine;
committed snapshots require a pinned runner. Stored p95 gates, historical trend
analysis, statistical significance, and cross-machine normalization are deferred.

The normal `--timeout` bounds the complete async test, not an individual round;
it cannot interrupt synchronous or CPU-bound work. Passing measurements flow
through `benchmark.py` into each outcome's `benchmarks`, the console result line,
and `benchmark_measurements` in JSON output. Completed measurements also remain
attached to failed and error outcomes.

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
