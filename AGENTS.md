# AGENTS.md

## Project Overview

snektest is a Python testing framework with first class support for async and static typing.

## Authoring tests

Before adding tests, read `uv run snektest --agent-docs` and print the relevant
bundled example (`uv run snektest --example parametrize` or `--example basic`).
Copy canonical shapes rather than assuming pytest-compatible constructor or
assertion arguments. Validate new parameter selectors with `--collect-only` and
run the chosen case; documentation snippets must also pass the existing strict
ty/execution checks in `tests/meta/test_doc_blocks.py`.

## Development Commands

### Complete release gate

```bash
uv run --locked python scripts/release_check.py
```

This is the one release-health command locally and in GitHub Actions. It checks
the lock, Ruff formatting and lint, ty, production dependency advisories, the
complete canonical `tests/` suite under coverage and again with forced lazy imports,
the 90% core-package threshold,
and independently built and installed wheel and source archives. CI runs it on
Python 3.15 under Linux, macOS, and Windows with a 15-minute outer timeout.

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

# List selectors without running bodies
uv run snektest --collect-only

# Stop on the first actionable failure
uv run snektest --fail-fast

# Print the ten slowest completed selectors
uv run snektest --durations 10
```

Every filter must select at least one test by default. `--allow-empty` permits
empty files, directories, marker selections, and filters only when intentional;
explicit missing test-name and parameter-case filters remain errors. Empty parameter-case brackets are rejected during argument parsing in all modes, including with `--allow-empty`.

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
This project uses `uv` for dependency management. Install all locked development dependencies with `uv sync --locked --group dev`; this includes the required strict `ty` checker. The project requires Python >=3.15. The verified support matrix is GIL-enabled CPython 3.15 on Linux, macOS, and Windows. Free-threaded CPython and other implementations are unsupported; newer CPython versions are not yet verified.

Migration toolchain rationale and local/downstream validation for Python 3.15
are recorded in `docs/python315-migration.md`.

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

1. **CLI Entry** (`_bootstrap.py:main`): Establish standalone startup policy before
   calling `cli.py:main` to parse args, create filters, and start the event loop.
   Executable and module entry points default context-aware warnings to enabled;
   opt out with `-X context_aware_warnings=0` or
   `PYTHON_CONTEXT_AWARE_WARNINGS=0`, with Python's normal precedence and `-E`/`-I`
   semantics. Imports, programmatic runners, and direct `cli.main` calls retain
   host policy. When changing startup precedence, relaunch, streams, entry points,
   or signal handling, read `docs/cli-startup.md` and exercise real CLI, embedding,
   and independently installed artifact seams. Keep opt-outs and workers aligned;
   retain inherited ignored SIGINT/SIGQUIT/SIGHUP rather than broadening signal
   policy here. Caught handlers follow exec/spawn semantics; avoid bootstrap exec
   for intentional in-process custom handlers. Test real shell background jobs
   and child signal state, not just runner handlers. Coordinator-only SIGINT in
   worker mode may terminate children without fixture teardown; final interruption
   JSON is not evidence of graceful worker cleanup. The tested SIGINT-only reset
   launcher and signal matrix live in `docs/cli-startup.md`.
2. **Canonical Collection** (`collect_tests_from_filters`): Resolve one canonical path identity for absolute or relative filters, support package-relative imports through an isolated synthetic namespace, import each module once per run, sort directory candidates by normalized path, preserve source and filter order, finish all imports before execution, reject empty filters/plans unless explicitly allowed, reject invalid or colliding parameter cases, and assign an ordinal to every occurrence. Later in-process runs import modules fresh; failed imports roll back. Imported decorated functions are excluded by module ownership. Captured import output and warnings remain separate collection diagnostics in normalized runs and structured output. Git ignore checks use each symlink's discovery pathname, not its target. Unexpected Git filtering failures inside a worktree are collection errors. Explicit files bypass Git ignore checks; repeated filters remain repeated cases. Bare `@test` raises during collection rather than disappearing.
3. **Local Execution**: With workers omitted, pass the complete plan directly to `run_tests`, with no cross-thread callback queue. It executes sequentially in process on one event loop. Fail-fast stops after a failure, error, XPASS, or function teardown failure; SKIP and XFAIL continue.
4. **Process Execution** (`parallel.py`): Explicit workers use spawn. One persistent
   host owns canonical collection and run fixtures; N persistent execution workers
   independently recollect and validate the manifest, each owning an event loop and
   session registry. Publication (`run_fixture_publication.py`) owns descriptor/failure
   caches, complete stage/release batches, and replacement replay. Repeated commits
   caused by requests crossing in-flight releases preserve existing worker-local copies.
   The scheduler hands it connections only after active scheduler reads finish.
   Concurrent async run-fixture loads share one in-flight request per identity. The
   worker publication module owns command reception and wraps case execution so
   cancelled waiters cannot abandon readers: outstanding loads drain before returning a
   result or receiving another command. Replacement workers restore published
   descriptors and cached failures before receiving work; each descriptor replay uses
   the configured timeout, and failed replay stops that child and aborts the run.
   Decoder exceptions use bounded, safe diagnostics even when their message formatter
   raises. The coordinator schedules mutex-compatible ordinals and reports in manifest
   order. Active and completed-but-unreported cases are capped at twice the worker
   count, which bounds the result backlog behind an early slow case. Fail-fast lowers
   dispatch to one active case so a later body cannot start.
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

Managed async generators use `AsyncFixtureDriver` (`async_fixtures.py`): one
persistent fixture-owned task advances setup and teardown, with a shared setup
result and an explicit teardown signal. This preserves task-local scopes and
ContextVar tokens across yield in every scope. Context changes stay in that
task, not the loading test; yield resources explicitly. Sync fixture loading
remains synchronous, so runtime scopes spanning yield require async fixtures.
Failed setup attempts retain ownership and dependency ordering; validation after
a run descriptor's yield still receives same-task teardown. `start_test()` opens
the function lifetime; function loads stay rejected after its teardown starts,
including during test-task cancellation. The fixture host awaits command reads
off-loop within one continuously running loop, so hosted async services remain
live between publication requests.

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
  descriptor-copy contract. Run-fixture setup preserves interruption instead of converting it to a publication error. Established dependencies still tear down. Worker mode stops further dispatch, drains active work, tears down worker sessions and host fixtures, then propagates the interruption.
- **Fixtures depending on fixtures**: a fixture may `load_fixture()` another in
  its body (resolved through the ambient registry). The dependency is registered
  for teardown after its own setup attempt finishes, so it lands below the
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

### Managed temporary directories

`temporary.py` owns `temporary_directory` allocation, retention and off-loop
removal; `FixtureRegistry` supplies active test/session lifetime. Finalize function
roots only after ordinary fixture/task/background classification, session roots
after ordinary session teardown. When changing lifetime inference, outcome rules,
ownership checks, cancellation, worker transport or retained-path reporting, read
`docs/temporary-directories.md` and exercise real local/worker CLI/JSON, embedding
cancellation/progress, and installed-wheel seams. Preserve path-only reporting,
explicit base placement and cleanup-error attribution without global TMPDIR
mutation or claims of cleanup after process death/stalled filesystem IO.

### Keyword selection

`-k EXPR` / `--keyword EXPR` uses `keywords.py` to compile a pytest-style
case-insensitive substring expression without eval or recursive parsing.
Operators are lowercase `not`, `and`, `or`, in precedence order; parentheses
group. Terms accept Unicode word characters and `: + - . [ ] /` and backslash.
Empty expressions match all. Repeated flags and malformed syntax are usage
errors before imports. No persistent keyword config is supported.

Collection validates positional selectors before globally intersecting keyword
and marker selection. A file with no keyword matches is allowed if the combined
selection is nonempty; empty positional files still require `--allow-empty`.
Missing explicit functions/cases always fail. Searchable names are function names
with bracketed case IDs, markers, and individual project-relative file/directory
components. Root is the nearest `pyproject.toml`, or cwd; exclude checkout/parent
names and use only the basename for external files. No arbitrary attributes or
parameter-value repr matching. `-k slow` also matches names; `--mark slow` only
matches markers. Syntax supports neither regex/globs nor marker arguments.

Selection precedes ordinal assignment for the final plan, retaining order and
repeated occurrences. Host, original workers, replacement workers, local runs,
and collect-only share selection. Workers accept empty positional filters already
validated by the host and check its manifest. Valid keyword expressions do not
skip imports or schema generation. Programmatic runners accept `keyword=`.

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
uses children. `--pdb` with workers or `--json-output` is a usage error. `-s --json-output` is supported; uncaptured process output is quarantined inside the JSON document.

### Async Hygiene

`task_cleanup.py` owns task tagging, descendant discovery, and bounded cancellation.
`task_scope(owner)` assigns the innermost test or fixture identity through one
context variable; `cancel_owned_tasks` discovers and reaps only that owner.
Execution and fixtures retain result attribution and lifetime ordering, without
separate ownership sets or cross-module task-exclusion checks. Hypothesis retains
its thread handoffs and uses the same cancellation machinery for active examples.
Function fixtures tear down before test-owned tasks are classified. Tasks created
during fixture setup inherit that fixture's owner and remain alive through its
teardown; session-owned tasks may survive between tests.
Failed setup retains ownership before its first yield. Each attempted function
or session generator stays on its scope's teardown stack, independently of the
session cache, so retries cannot discard earlier cleanup. At scope teardown,
abandoned tasks produce fixture-attributed diagnostics alongside the original
setup error. Shared async session setup remains fixture-owned after a test cancels
its waiter, including between tests. Scope teardown cancels unfinished setup under
the cleanup budget before tearing down dependencies. Function setup still pending
when its test returns counts as abandoned fixture work; cooperative cancellation
of unfinished cached setup is normal scope shutdown. Resistant
setup and cancellation/finalizer errors receive fixture-attributed teardown
diagnostics. Descendants created during forced setup finalization retain their
fixture owner. Fixture authors should protect background-task setup and the yield
with `try`/`finally`.
A fixture that returns while its tasks remain pending receives an attributed
teardown failure. Test-owned leaks are cancelled after function teardown. Owned-task cleanup re-scans cancellation-created descendants under one deadline. Forced finalizers run in their original task context so descendants retain ownership. If tasks survive the final close/reap attempt, the command stops rather than starting another test. Unrelated embedding tasks are left alone. New
tasks from an unrelated embedding application have no test owner and are left
alone. Asyncio cancellation waits are bounded; a resistant coroutine is force-closed
and the owning test or fixture fails. Trio instead fail-stops if owned work
survives the budget; native shutdown still needs an external deadline. Errors raised while force-closing abandoned tasks are retained as `task_cleanup` background diagnostics for tests, or individual fixture teardown failures. Cleanup continues to other tasks and later tests.

### Python-native typing contracts

`type_contracts.py` owns bare `@test_typing` and `assert_type_error(rule)`.
Bodies are statically analyzed, never executed; function parameters are symbolic
inputs. Both top-level and implementation-module stubs expose the decorator as
`typing.no_type_check` for ordinary checks, including direct submodule imports. Static runs replace only that declaration through a temporary
partial stub and analyze original source with the optional pinned ty binary.
Preserve original imports/locations, exact per-block diagnostic cardinality,
innermost ownership, selected-function isolation, and shared positive controls.
Resolve direct Snektest imports with Python symbol tables, including local
imports and closures, rather than mutable runtime globals. Marker values copied,
re-exported, or dynamically looked up are unsupported. Ownership uses body-only
line/Unicode-column spans; with-header diagnostics cannot certify rejection.
Select declarations by code name/first line, not mutable function names. Source
fingerprints are captured at registration and checked before/after ty; detected
edits are errors, not atomic protection against concurrent filesystem writers.
Broken imports cannot certify a rejection. Keep checker imports/invocation out of
collection and runtime-only runs. Each checker subprocess has bounded output and
must be killed/reaped on cancellation; this does not bound local collection.
A private session fixture retains at most one validated file report per execution
process/run. Key reuse by source fingerprint and explicit config content; later
runs/replacement workers start fresh. `--typing-config FILE` and programmatic
`typing_config=Path(...)` accept native ty config, not persistent Snektest config.
Default to the current interpreter unless native `environment.python` explicitly
selects another; preserve native extra-paths. Stage only the framework package,
never its hosting site-packages directory, so selected environments cannot inherit
unrelated host libraries or shadow target versions. Non-standard sys.path additions
need explicit native extra-paths. The deprecated/type-assertion-failure/
unresolved-import error invariants remain enforced. Inspect every per-file rule
override: native overrides outrank CLI severities, so reject relaxations of the
protected rules (including all=ignore/warn without explicit restoration) as run
errors. Keep native syntax/type diagnostics and fail closed on uninspectable
configuration. Preserve run-local context through worker
replacement. Require a single bare decorator; other contexts do not declare
expected errors. The temporary stub uses an internal deprecated reference probe
to reject known copied/re-exported marker escapes without executing Python.
Drain cancelled report streams before returning, not just the process exit:
paused transports can retain pipe descriptors. For checker changes, read
`docs/type-contracts.md` and exercise the public marker/decorator, real runner,
and independently installed artifact seams.

### Explicit log capture

`log_capture.py` owns `capture_logs` / `LogCapture`: opt-in stdlib capture with
original-record and safely rendered-message tuple snapshots. The handler lock
serializes emission, snapshots, and retirement; the process-local registry lock
protects overlapping captures and restores levels in parent-before-child order.
Keep these locks inside synchronous entry/exit operations, never across awaits.
Application handlers remain attached; only capture-owned handlers are removed.
Skip unchanged level writes. Retire registry ownership before fallible restoration
and refresh other scopes even if an application logger setter raises.
When changing capture thresholds, scope retirement, nesting, rendering, or logger
restoration, read `docs/log-capture.md` and exercise the public logger, real
runner/worker, and installed-package seams. Preserve explicit retention (no
implicit result attachments or redaction claims).

### Thread Observability

Execution temporarily installs and chains `threading.excepthook` and
`sys.unraisablehook` around each canonically sequential test, including function
teardown and task cleanup. An unhandled thread or unraisable exception turns a
pass into an error. New non-daemon threads alive afterward turn a pass into a
failure; daemon and current event-loop default-executor threads are exempt. Registered idle
AnyIO workers on that loop are also exempt by identity, never by thread name.
New busy AnyIO workers and raw lookalikes retain the ordinary leak diagnostic.
Additional background failures on an existing failure/error remain process-safe
`BackgroundFailure` diagnostics in console and JSON. Broken exception message formatters cannot hide thread or unraisable failures; diagnostics use a safe fallback and still chain the previous hooks. Hooks are process-global,
so concurrent direct `execute_test` calls are unsupported. The standard runner
never overlaps tests within one process.

The supported blocking pattern is `await asyncio.to_thread(...)`. Context is
copied there, but fixtures should still be loaded before offload; raw
`threading.Thread` does not inherit the fixture-registry context. Threads cannot
be cancelled. A timed-out `to_thread` await leaves its function running and it
may delay event-loop shutdown. Raw leaked threads, sync or CPU-bound hangs,
imports, and sync teardown still need an outer process/CI timeout. There is no
threaded test executor.

### AnyIO and backend policy

When changing AnyIO thread recognition, fixture integration, or async runtime
selection, read `docs/async-backends.md`. `--backend asyncio|trio`, project
`backend`, and programmatic `backend=` are explicit and command-wide; asyncio
remains default. Require a matching embedding host and optional Trio dependency;
reject unavailable/unknown engines before imports without fallback. Propagate
choice through host/original/replacement workers and publication. `runtime.py`
and `runtime_tasks.py` own native engine/task seams. Trio cleanup is cooperative:
owned work surviving its budget raises infrastructure failure before another
case, never cross-task coroutine closure. Shielding may postpone body deadlines;
native shutdown can still need an outer process timeout. Preserve asyncio's
existing cleanup behavior and unrelated embedding tasks. Async fixture tasks may keep scopes
across yield; fixture-local context/cancellation is not the test's context.
Warnings are buffered by their fixture task and relayed at setup/teardown;
later fixture-background warnings belong to its teardown. Run-host setup
warnings travel with existing setup output. Respect capture opt-outs and avoid
lifetime warning catchers when context-aware warnings are explicitly disabled.
`thread_observation.py` isolates the private AnyIO RunVar identity lookup, reads
only an already-loaded backend, and fails closed on unknown registry shapes.
Preserve raw-thread/busy-new-worker leak checks; do not prewarm, daemonize, rename,
or shut down a caller-owned pool. Existing-thread work is not fully tracked.
The optional extra and explicit dev dependency select AnyIO `>=4.14.2,<5`;
`snektest[trio]` selects Trio `>=0.34,<0.35`. Trio's native daemon thread cache
follows the existing daemon exemption; abandoned offloads may outlive the runtime.
Exercise real local/worker/embedding, Hypothesis/schema/typing/memory/progress,
and independently installed core-only/extra seams when changing these adapters.

### Timeouts

CLI runs apply a 60-second timeout to every async test by default.
`--timeout SECONDS` accepts a finite positive value, while `--no-timeout`
disables the test-body limit. The CLI passes that run-wide ceiling to
`execute_test`, which wraps the awaited body in the selected native deadline. It is
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
after cleanup; explicit test-raised `CancelledError` remains a failed test. Cancellation arriving during cleanup, including repeated requests, waits for the bounded cleanup phase to finish. Later fixture scopes still receive teardown attempts.
Synchronous teardown cannot be interrupted on the local event-loop thread and
requires an outer process or CI timeout.

Interactions:

- **`@test_hypothesis`.** An async property test runs every example inside a
  single native thread offload (`decorators.py`), so the
  timeout wraps the whole property run, not each example. Copy sender ContextVars
  per handoff, not a frozen loop-entry context. `hypothesis_context.py` binds the
  worker's existing build context, settings and reporter around each coroutine
  send/throw/close, restoring them before suspension. Preserve native yield tokens,
  task identity and finalizer errors. Never re-enter BuildContext (it owns cleanup),
  hold thread-local bindings across awaits, or install them in unrelated tasks.
  Retain the worker build context through the bounded example-cleanup attempt;
  always acknowledge its retirement, preserving infrastructure errors over drain
  timeouts. Controls apply to the example task/directly awaited helpers, not separate tasks,
  threads or async fixtures. Exercise real shrinking/notes, cancellation, embedding
  isolation and installed archives when changing this private Hypothesis seam. If it fires while an
  example is suspended, task cancellation completes the cross-thread handoff and
  lets the Hypothesis worker stop. Cancellation stops new property examples and completes their cross-thread handoffs before bounded cleanup. Asyncio resistant examples are force-closed under the cleanup budget, and finalizer errors remain task_cleanup diagnostics; Trio fail-stops instead. Blocking thread work still needs an outer timeout. Synchronous or CPU-bound work in that thread
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

Use `Param(value=..., name=...)`: one named value per argument. A tuple value
carries related inputs/expected output to one tuple-annotated argument; separate
lists supply separate arguments and their Cartesian product, never pairwise zip.
Missing names (`Param(value="sqlite")` / `Param("sqlite")`) require an explicit
stable name; multiple values (`Param(1, 2, name="pair")`) require a tuple value.
Positional construction with both required fields remains supported. Never infer
IDs from value reprs. Print `--example parametrize`, save it as
`test_parametrize.py`, and use `--collect-only` to obtain exact rerun selectors;
quote bracketed/multi-axis selectors in shells. `--agent-docs` is the embedded
usage guide, not a separate workflow to maintain.

For a `Literal`-annotated argument, preserve the union in the axis type, e.g.
`type Backend = Literal["sqlite", "mariadb"]` and
`BACKENDS: list[Param[Backend]] = [...]`, or use `Param[Backend](...)` explicitly.
An unconstrained axis may widen to `Param[str]`, which cannot safely feed that
narrower argument; casts, `Any`, and checker ignores are not the remedy.

Tests can accept multiple parameter sets via `@test([...], [...], mark=...)`,
each non-empty list built from `Param(value=..., name=...)`. Static checking
preserves parameter types through two lists; three or more use the variadic
`Any` fallback. Case names are
non-empty, unique, and exclude `, `, `[` and `]` so CLI filters are unambiguous.
`Param.to_dict()` creates all combinations using `itertools.product`; each
combination becomes a separate test execution and cardinality is the product of
axis sizes. One decorated test may produce at most 10,000 cases. Larger products
raise `BadRequestError` before expansion. The limit is per test, not per suite.

### GraphQL contract testing

`test_graphql` (`schema.py`) uses optional `snektest[schema]` dependencies to
load local SDL or introspection JSON and collect one positive generated case per
selected root field. Introspection accepts raw `__schema` objects and `data`
wrappers; non-empty response errors reject the export even with usable data.
Files are UTF-8 with optional BOM. JSON is content-detected, while `.json` files
must parse as JSON. Collection never fetches introspection from the endpoint.
Queries are the default; `allow_mutations=True` permits mutations before filtering.
`operations=GraphQLFilter(...)` uses exact `GraphQLOperationSelector(kind=...,
field=...)` matches. Criteria are AND, include/exclude tuples are OR, and excludes
win. Filters never enable mutations on their own. Kinds work with custom root
type names; field names are case-sensitive. Empty selections fail collection.
Subscriptions are excluded. The body is metadata-only; `url` is
the complete endpoint and may be a literal or sync/async fixture handle.
Each case runs Hypothesis in a worker thread, preserving the main event loop for
fixture-started services. Native server-error checks reject HTTP 5xx, malformed
JSON, non-object responses, and GraphQL errors even with HTTP 200 or partial data.
Failure groups become `AssertionFailure` with the root field, generated query,
and HTTP status for server errors;
transport/configuration errors remain errors. Returned field types are not fully
validated against SDL. Generated values and server messages may be sensitive.
`headers` accepts literal dictionaries or sync/async fixture handles, resolved
before the worker thread starts. `auth` registers a native Schemathesis provider
class on the loaded schema; `checks` adds native checks without replacing defaults.
Provider and check callbacks run in the Hypothesis worker thread. Custom assertion
failures become contract failures; other callback exceptions remain errors.
Subscriptions and stateful sequences are unsupported. Generated mutations can
repeat during shrinking; use disposable services. Use ordinary tests for
application-specific response assertions.

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

### Large-suite memory

The serial runner retains one compact result per case but does not duplicate the
collected plan into a queue. Console reporters release captured output from
clean passes after each progress result; JSON, JUnit, and programmatic runs
retain it for completed-run adapters. Failure output remains available. Normal
runs clear locals from ended traceback frames after creating bounded immutable
diagnostics. `--pdb` alone keeps live tracebacks and stops on the first failure.
Process dispatch permits at most twice the worker count in active or
completed-but-unreported cases, or one case in fail-fast mode. Measured budgets
and the reproducible benchmark command live in
`docs/large-suite-memory.md`. Lazy first-use initialization has a separate cold-run
budget; the warmed noisy-output run retains its original allocation limit.

### Lifecycle progress

`progress.py` owns opt-in `--progress-output PATH` JSONL observation, native
cross-process append locking, bounded metadata/snapshots/output, and heartbeat
threads independent of result reporters and descriptor capture. Fresh paths only;
`--progress-interval` is finite and at least 0.05 seconds (default 5). When changing
phase instrumentation, record identity, truncation, writer retirement, child spawn,
or terminal semantics, read `docs/progress.md` and exercise real gated subprocesses
in `tests/meta/test_progress.py` plus independently installed artifact entry points.
Keep selector/ordinal/attempt and worker-incarnation identities distinct. No locks
span awaits; teardown events use the cleanup caller's context, not an async
fixture driver's stale setup context. Retired writers ignore late collection-thread
events. Missing terminal events and unmatched child spans remain meaningful;
terminal observation does not certify fixture cleanup or physical process exit.
Preserve stdout JSON version 1 and the existing completion reporter interface.
Heartbeat threads cannot enforce hard deadlines or prove deadlock, and must not
inspect frames, locals, captured output, or exception messages. No persistent
progress config or top-level observer types are introduced by this slice.

### Structured output

Warning message formatters that raise use a safe text fallback. Capture always restores standard streams and debugger hooks even if exit-time warning rendering fails.

`RunResult` is the normalized completion consumed by console, JSON, and JUnit
adapters and returned by `run_tests_programmatic`. It owns status and teardown
counts, total duration, collection diagnostics, test results, and session/run
teardown diagnostics. Function teardown counts use individual failures, not
affected tests.

`--json-output` writes one schema-version-1 document to stdout for runs and CLI
errors. It retains framework version, exit code, every status, output, warnings,
markers, measurements, and bounded exception tracebacks. A command-level file
descriptor guard quarantines uncaptured stdout/stderr, child output, and direct
descriptor writes as `uncaptured_output`; malformed UTF-8 uses replacement
characters. `--junit-output PATH` maps the same `RunResult` to JUnit XML and emits
one synthetic error case per fixture teardown failure. JUnit replaces XML 1.0-forbidden characters, including NUL and lone surrogates, with U+FFFD in text and attributes. JSON and programmatic results retain the original strings.

`--collect-only` lists canonical selectors without executing bodies and emits a
versioned `collection` document with `--json-output`. `-x` / `--fail-fast` stops
on actionable outcomes without opening PDB. `--durations N` orders completed
results by duration and prints exact rerun selectors. `RunResult.selected_tests`
and `stopped_early` distinguish the selected plan from cases that ran.

`configuration.py` finds the nearest `pyproject.toml` and validates
`[tool.snektest]`. It accepts `test_paths`, `backend`, `timeout`, `mark`, `capture_output`,
`json_output`, and `junit_output`; unknown keys fail. Relative paths use the
project directory. CLI filters and flags, including inverse output and marker
flags, override configured values. `--output-schema-versions` lists supported
JSON schema versions.

`collection_errors.py` owns bounded, user-first collection console reports.
`--full-traceback` adds framework/dependency frames, including child-host details,
without changing JSON snapshots or collection behavior. When changing frame
selection, syntax/cause/group rendering, work/output limits, or host transport,
read `docs/collection-errors.md` and exercise real local/worker console and JSON
subprocesses in `tests/meta/test_collection_diagnostics.py`. Preserve selected-file
context, original error types, explicit truncation, and the exit-2/no-body contract.

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

`equality.py` owns failure-only, bounded explanations for `assert_eq`. Inspect
only exact built-in values; preserve the assertion's original `!=` operation and
never invoke additional user repr/equality/iteration hooks for diagnostics.
Equality (`operator="=="`) messages are complete: snapshot/render them as plain
messages without inspecting operands again. Keep JSON schema version 1 and
non-empty custom messages unchanged. When changing string/key/index explanations,
preview safety, or work limits, consult `docs/equality-diagnostics.md` and exercise
the public assertion and CLI/worker tests.

The narrowing helpers return the narrowed value so a single call both asserts and
narrows under the strict ty config: `assert_is_not_none(x)` returns `x`
typed as non-`None`, and `assert_isinstance(obj, SomeType)` returns `obj` typed as
`SomeType`. Bind the result (`opts = assert_isinstance(result, CliOptions)`) to
narrow for later attribute access; for a pure assertion discard it
(`_ = assert_isinstance(x, int)`) to make that intent explicit.

`assert_raises(*expected_exceptions, msg=None, match=None)` accepts string regexes
and compiled `re.Pattern[str]` values. `match` searches `str(exception)` only after
type validation, preserves compiled flags, and accepts an empty pattern for any
renderable message. `msg` overrides assertion failure text, not matching behavior.
Capture `.exception` for the original typed object and use `assert_eq` on its text
for exact equality. Match does not inspect notes or causes: bind
`cause = assert_isinstance(caught.exception.__cause__, ExpectedError)` and assert
on that original cause explicitly. A different `msg=` does not prevent a passing
match or replace assertions on the raised text. Print `--example basic` for exact
text, regex, and cause examples. Mismatches or broken message formatters raise
`AssertionFailure` with the original exception as cause; fallback text is never
matched. Invalid regexes and bytes patterns raise `BadRequestError` at context
construction, before the body. The top-level stub re-exports the implementation's
generic signature; installed-wheel tests verify capture types and reject bytes
and non-pattern arguments.

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

Measurement lifetime: Measurement contexts are single-use, including after failed entry or exit. Create a fresh `assert_memory` or `assert_benchmark` context for each region. Access and advance `.rounds` only while that context is active; saved iterators cannot resume after exit.

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
unselected entries; marked or keyword-filtered runs replace only observed tests. Baseline identity is
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

## Migration contract

The 0.16 to 0.17 notes must cover the Python 3.15 minimum (dropping Python 3.14),
the canonical `SnektestError` hierarchy and `Scope`, SKIP/XFAIL/XPASS semantics, normalized `RunResult`, JSON schema version
1, JUnit output, and per-fixture teardown counts. Keep the README and embedded
agent guide synchronized when this contract changes.

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

### Import policy

Eligible runtime imports, including bundled examples, use explicit `lazy` syntax.
Future imports, stubs, and syntax-restricted contexts remain eager. Definitions
may resolve imported names immediately; Ruff TID255 is intentionally disabled
because explicit syntax is the convention, not a promise of universal deferral.

`import_policy.py` owns synchronous, serialized initialization scopes. Collection
realizes top-level imports in the synthetic test namespace; property/schema engine
internals register eagerly within collection and schema-factory initialization.
The scope delegates unrelated imports to the existing filter and restores it on
all exits without changing the interpreter mode. Never span an await or a
fixture/test lifetime with this scope. It does not fix unrelated dependency
imports for embedding applications. See `docs/lazy-imports.md` for policy and
normal/forced-lazy validation.

### Code Style Notes

- Ruff with extensive rules enabled (see pyproject.toml:22-64)
- Tests allow magic numbers, assert statements, private access (see per-file-ignores)
- Line length 88, but E501 ignored (long strings for messages okay)
- Mixed case names allowed in `annotations.py` for validators like `validate_SomeType`
