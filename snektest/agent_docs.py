"""Embedded documentation and examples for coding agents."""

lazy from importlib.resources import files

lazy from snektest.models import BadRequestError

AGENT_DOCS = """# snektest agent guide

Snektest requires Python 3.15 or newer. The verified release matrix is GIL-enabled CPython 3.15 on Linux, macOS, and Windows. Free-threaded CPython and other implementations are not supported; newer CPython versions are not yet verified.

## Import behavior

The runtime and bundled examples use explicit lazy imports. Importing the package,
ordinary test decorators, and CLI help/version defer property/schema engines.
Definitions may still immediately resolve individual imports. One-time loading
costs move to first use; memory regressions separately bound cold and warmed runs.
The release gate also runs `python -X lazy_imports=all -m snektest tests`.
Collection eagerly
completes top-level imports in selected test modules and their synthetic package
namespace, including explicitly lazy imports, so import failures/output/warnings
stay collection diagnostics. Function-local imports remain execution-time work.
Engine registration uses a scoped filter that preserves unrelated import policy
and restores the caller's filter on exit; the interpreter mode is unchanged.
Unrelated third-party lazy-import compatibility remains application-owned.

## CLI startup

The executable, `python -m snektest`, and `python -m snektest.cli` enable
context-aware warnings before collection and worker startup. No environment
variable is needed for ordinary runs. Explicit Python startup choices win:
`python -X context_aware_warnings=0 -m snektest ...` or
`PYTHON_CONTEXT_AWARE_WARNINGS=0` opts out; `-X` beats the environment, while
`-E`/`-I` ignore the environment and still apply the default. Already-enabled
interpreters and explicit opt-outs do not restart.

Otherwise the CLI relaunches the same interpreter/venv with its options,
arguments, cwd and streams preserved. POSIX replaces the process without resetting
inherited ignored signals; Windows waits for the child and propagates its exit
status. Interpreter/site startup hooks may repeat, before collection. Importing
Snektest, programmatic runners, and direct `snektest.cli.main` calls keep the host
policy unchanged. This is startup policy, not hard interruption or cleanup.

## POSIX signals and background jobs

The CLI preserves inherited ignored SIGINT, SIGQUIT and SIGHUP; it does not
normalize background-job signals. Noninteractive shell `&` jobs commonly ignore
SIGINT/SIGQUIT; `nohup` concerns SIGHUP, not a SIGINT reset. Normal SIGINT requests
interruption (exit 2, an `interrupted` JSON error when shutdown completes).
Caught custom handlers cannot survive exec or worker spawn; an in-process custom
handler is preserved when no bootstrap relaunch is needed. Application signal
tests should explicitly establish their child's signal state before exec instead
of changing runner-global handlers. This applies locally and with workers.

A SIGINT sent only to the worker coordinator may terminate children without
fixture teardown. Local cooperative cleanup is bounded for async work, not hard
interruption of arbitrary sync code. SIGKILL/hard interruption may prevent cleanup
and final JSON; Snektest does not contain arbitrary subprocess trees.
See [CLI startup policy](docs/cli-startup.md#posix-signal-policy-108) for the tested
SIGINT-only background reset recipe and foreground/background/custom-handler
semantics. There is no signal-normalization flag; use an explicit launcher when
interruptible background execution is desired.

## AnyIO and async runtimes

Install `snektest[anyio]` for AnyIO `>=4.14.2,<5`. Asyncio is the default.
For native Trio, install `snektest[anyio,trio]` and select `--backend trio`.
Await AnyIO APIs directly, without `anyio.run()` or a nested event loop:

```python
from anyio.to_thread import run_sync

from snektest import assert_eq, test


@test(mark="medium")
async def test_anyio_offload() -> None:
    assert_eq(await run_sync(lambda: 42), 42)
```

Managed async fixtures keep setup, yield, and teardown in one fixture-owned
task in every scope. AnyIO task groups, cancel scopes, and library async context
managers may span their yield. Use try/finally and stop forever-running children
before leaving the task group. Fixture tasks inherit their loader's context,
but their ContextVar changes stay fixture-local; yield resources/context values
explicitly instead of implicitly binding the test's context. Fixture cancel
scopes govern fixture work, not the test body. Synchronous fixtures still load
synchronously; runtime scopes spanning yield require async fixtures. Function
loads after function teardown starts are rejected, even in task finalizers.
Idle pool
workers registered with the current asyncio loop are exempt from thread-leak
checks by identity, not by name. New busy/abandoned workers and raw lookalikes
remain failures. Existing-thread work is not comprehensively tracked; threads
cannot be forcibly stopped. AnyIO is optional and core observation does not
import it. Print `--example anyio` for a fixture-backed async service.

`--backend asyncio|trio` is explicit and command-wide, including fixture host,
original/replacement workers, tasks and deadlines. Trio requires `snektest[trio]`
(`>=0.34,<0.35`); missing/unknown engines fail before imports without fallback.
The project default is `[tool.snektest] backend = "trio"`; `--backend asyncio`
overrides it. Asyncio-specific libraries still require asyncio. Save `--example
anyio` to a test file and run it with either backend.

Embedding uses `snektest.cli.run_tests_programmatic(..., backend="trio")` on a
matching native Trio or AnyIO-with-Trio runtime. Omitting backend means asyncio,
not autodetection. The caller owns its runtime; no nested loop is started.
Trio cleanup is cooperative and aborts before another selected case if owned
work survives its budget, without cross-task force-close. Shielding can postpone
body timeouts; native shutdown can still require an outer process deadline.
Trio's native daemon thread cache follows the existing daemon exemption; offload
cancellation is never thread termination. Asyncio cleanup behavior is unchanged.
See [native backend policy](docs/async-backends.md) for lifetime/shutdown limits.

## Quick start

Create a `test_*.py` file and decorate test functions with `@test(mark=...)`. Mark every test with the resources it may use:

```python
from snektest import assert_eq, test

@test(mark="fast")
def test_addition() -> None:
    assert_eq(1 + 1, 2)
```

Install Snektest and the required strict type checker, then run both checks:

```bash
uv add snektest
uv add --dev ty
uv run ty check
uv run snektest
```

Repository contributors install every locked development dependency with
`uv sync --locked --group dev`.

Useful commands:

```bash
snektest --help
snektest --version
snektest --output-schema-versions
snektest --collect-only
snektest -k "fixture and not teardown" --collect-only
snektest --fail-fast
snektest --durations 10
snektest --agent-docs
snektest --examples
snektest --example async
snektest --example anyio
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

## Python-native type contracts

Install `snektest[typing]` to check consumer typing behavior with the pinned ty
checker. Use bare `@test_typing` (unlike `@test(...)`) on a module-level sync or
async function. Its body is analyzed, never executed; parameters are symbolic
inputs, not runtime fixtures. Static tests receive the `slow` marker.

```python
from typing import assert_type

from snektest import assert_type_error, test_typing


def takes_int(value: int) -> int:
    return value


@test_typing
def test_argument_types(symbolic: int) -> None:
    assert_type(takes_int(symbolic), int)
    with assert_type_error("invalid-argument-type"):
        takes_int("wrong")
```

Each assertion block requires exactly one diagnostic with the literal rule name.
Missing, different, or additional diagnostics fail; nested blocks own their
innermost diagnostics. Only diagnostics inside the block body count, not errors
in its `with` header. Unexpected errors in the selected function or shared
module helpers fail too. Failures include original source locations. Broken
imports and checker setup are errors, not proof of a successful rejection. The
marker is not supported in ordinary runtime tests.

Use direct Snektest imports for assertion markers: imported aliases and qualified
module imports work, including imports inside static bodies. Marker resolution
uses source bindings rather than mutable runtime globals. Copied, re-exported,
and dynamically looked-up marker values are unsupported. Keep source files
unchanged during a run; detected edits are errors. Source checks are not atomic
filesystem protection against concurrent writers.

Run and select these functions through the usual Snektest commands, locally or
with workers. Ordinary `ty check` deliberately skips their bodies through both
package and implementation-module stubs; **run Snektest to validate the contracts**,
including positive `assert_type` assertions. The
static run checks the original source with a temporary partial stub, preserving
imports and source locations, using the current interpreter and isolated strict
rules rather than ambient ty configuration. Ordinary runtime tests and
`--collect-only` do not launch ty.

Use `--typing-config FILE` for an explicit native ty configuration, or pass
`typing_config=Path(...)` to `run_tests_programmatic`. Otherwise Snektest uses
isolated strict rules, not ambient project configuration or `TY_CONFIG_FILE`.
The checker defaults to the current interpreter; native `environment.python`
selects another interpreter/environment and `environment.extra-paths` selects
additional import roots. These are explicit native ty settings, not new Python
contract declarations. Only Snektest's package is exposed to the checker, not its
host's entire package directory. Non-standard runtime `sys.path` additions need
matching native `extra-paths`; they are not implicitly inherited.
`deprecated`, `type-assertion-failure`, and
`unresolved-import` remain errors even when the file relaxes other rules.
Every per-file override must preserve these three rules at `error`; overrides
that lower them directly or through `all` are configuration errors, even if their
patterns do not match the selected file. Restore the three rules explicitly when
using a per-file `all = "ignore"`. Ignored expected diagnostics still cause
missing-expectation failures. Config
parse/startup failures are errors, not successful type rejections. This option is
run-local, not a persistent `[tool.snektest]` setting.

Each execution process retains at most the most recent validated file report,
reusing it for same-source contracts in the same run. Later runs and replacement
workers start fresh; switching files can require another invocation. Keep all
source/dependency files unchanged during a run. Explicit configuration edits
invalidate reuse. This preserves selection and fail-fast without running bodies.

Use a single bare decorator; stacked decorators can disable analysis and are
rejected. Only the documented literal `with assert_type_error("rule")` form
(or its imported alias/qualified spelling) declares an expected error. Other
Python context managers are ordinary type checks, not rejection assertions.
Known marker escapes through copied/re-exported bindings and direct dynamic
lookups are rejected. Checker output and process waits are bounded; cleanup
kills/reaps the child and drains its pipes. Collection/imports still require an
external command timeout. See `snektest --example typing` for sync/async examples.

## Migrating from 0.16 to 0.17

Python 3.15 or newer is required; Python 3.14 is no longer supported. Recreate
existing virtual environments with Python 3.15 before upgrading.

Import public errors, `Scope`, decorators, assertions, and fixtures from the
`snektest` package. Exported errors now share `SnektestError`, `Scope` is the
canonical scope type, and internal invariant errors are no longer public.
SKIP and XFAIL now exit successfully, while strict XPASS exits 1. Static xfail
converts only Snektest assertion failures. Programmatic results add
`SkippedResult`, `ExpectedFailureResult`, and `UnexpectedPassResult`, with JSON
statuses `skipped`, `expected_failure`, and `unexpected_pass`. Programmatic
runs now return `RunResult`. JSON consumers must handle the version-1 envelope and canonical
status names; JUnit is available through `--junit-output`. Function teardown
counts now count each failed fixture teardown.

## Core patterns

- Import assertions from `snektest`; use the `assert_*` helpers, never bare
  `assert` (bare `assert` is banned in this repo's tests).
- Assertion argument order is intentional: pass the observed/computed value
  first and the expected/reference value second, following names like `actual`,
  `expected`, `member`, and `container`.
- `assert_eq` failures explain long strings with zero-based character context,
  multiline strings with expected-to-actual unified diffs, and exact built-in
  dict/list/tuple values with key/index paths. Only failures load the formatter.
  Non-empty `msg=` stays authoritative; empty messages use defaults. Generated
  explanations are capped at 8,000 characters with explicit limit notices.
  Custom objects and builtin subclasses stay opaque: diagnostics never call
  their repr or additional equality hooks. Console details and JSON version-1
  exception messages share the explanation. This does not normalize SQL or
  alter Python equality semantics.
- `assert_is_not_none(x)` and `assert_isinstance(x, SomeType)` return the
  narrowed value; bind it to narrow for later use, or discard with `_ =` for a
  pure assertion.
- `assert_raises(ValueError, match="pattern")` accepts a string regex or compiled
  `re.Pattern[str]` and searches `str(exception)` after checking its type; compiled
  flags are preserved. Empty patterns match any renderable message. `msg=` instead
  overrides assertion failure text. `.exception` preserves the original typed
  exception; use `assert_eq(str(caught.exception), expected)` for exact equality.
  Mismatches and broken message formatters raise `AssertionFailure` with the
  original cause; diagnostic fallback text is never matched. Invalid regexes and
  bytes patterns raise `BadRequestError` at context construction, before the body.
  See `--example basic` for regex, exact-text, and original-cause examples.
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
- Failed fixture setup retains task ownership before its first yield. At the fixture's scope teardown, abandoned tasks are cancelled and cleanup failures are reported separately from the setup error. Retrying a failed session fixture preserves cleanup for each attempt. Shared async session setup survives cancellation of a test's waiter and remains available to later tests. At scope teardown, unfinished setup receives bounded cancellation; resistance and cleanup errors become fixture teardown diagnostics. Function setup left pending when its test returns is abandoned fixture work and is cleaned before the next test. Put background-task setup and the fixture's `yield` inside `try`/`finally` so both setup failure and normal teardown stop tasks.
- Use `await asyncio.to_thread(...)` for blocking application work in an async test, and load fixtures before offloading. `to_thread` copies context; a raw `threading.Thread` does not inherit the fixture registry. An async timeout stops awaiting offloaded work but cannot stop its thread, which may continue or delay shutdown. Sync, CPU-bound, import, raw-thread, and sync-teardown hangs still need an outer process or CI timeout.
- Unhandled thread and `sys.unraisablehook` exceptions make an otherwise passing test an error. A new non-daemon thread still alive after function teardown makes it fail; joined threads pass, daemon threads, event-loop default-executor workers, and registered idle AnyIO workers on that loop are exempt. Existing hooks are chained and restored. Extra background failures remain visible in console and JSON diagnostics. Snektest does not kill threads or provide a threaded test executor.
- Use `Param(value=..., name=...)` for one named value per argument. See
  **Parameter values and case names** below for tuples versus separate axes and
  common construction mistakes. `@test_hypothesis` preserves strategy types
  through five strategies; six or more use its `Any` fallback.
- Define fixtures as generator functions decorated with `@fixture`, annotated `Generator[T]` or `AsyncGenerator[T]`. Load them with `load_fixture(fixture())` — call the decorated fixture and pass the returned handle.
- `@fixture` (default) is function-scoped. `@fixture(scope="session")` is cached once per execution process. `@fixture(scope="run")` is a zero-argument module-level fixture owned once by the command; it yields an inert stdlib-pickle descriptor of at most 1 MiB, and each worker receives an independent decoded copy. The literals and matching `Scope` enum members normalize to `Scope`; fixture handles expose that enum at runtime.
- Fixtures may take arguments; pass them at the call site, e.g. `load_fixture(make_user("Ada"))`. Calling a fixture twice gives two independent instances.
- Session and run fixtures must be zero-argument; use function fixtures for parameter-dependent setup, or return a factory/cache from a zero-argument cached fixture.
- A fixture may depend on another by calling `load_fixture()` in its body. Function may depend on function, session, or run; session on session or run; run on run only. An async fixture may depend on sync or async fixtures; a sync fixture cannot await an async dependency. A depending fixture is torn down before the fixtures it loaded.
- Put all `load_fixture(...)` calls at the beginning of the test, before actions or assertions.
- Avoid conditional or mid-test fixture loading unless delayed loading is the behavior under test.
- Collection and every selected module import finish before execution. Directory files are ordered by normalized path, cases retain source definition order, and filters retain command-line order. Absolute and relative paths share one canonical module identity, package-relative imports work, and imported decorated functions are not collected as local tests. Overlapping filters import a module once in one run; a later run imports it fresh. Captured import output and warnings remain separate collection diagnostics in normalized runs and structured output. Local execution consumes the completed plan directly without a callback queue. `-n COUNT` / `--workers COUNT` uses persistent spawn workers plus one collector/run-fixture host; `auto` selects the lesser of process CPUs and selected cases. Each worker owns one event loop and its session fixtures. Results stay in manifest order, with active and completed-but-unreported cases capped at twice the worker count outside fail-fast mode.
- `mutex="name"` on `@test` and `@test_hypothesis` prevents overlap for the same exact, non-empty, trimmed, case-sensitive command-local name. It is not an OS lock. Explicit workers cannot be combined with `--pdb`; `--json-output` cannot be combined with `--pdb`.
- Catch exported framework failures with `SnektestError`. `AssertionFailure`, `BadRequestError`, `CollectionError`, `FixtureError`, `SchemaGenerationError`, and `TestTimeoutError` share this ordinary `Exception` base; `AssertionFailure` also remains an `AssertionError`. Internal invariant errors are not exported.
- Console runs discard captured output from clean passing tests after reporting each result. JSON, JUnit, and `run_tests_programmatic` retain passing output. `RunResult` is the one normalized completion consumed by every adapter. Normal runs retain bounded exception snapshots and clear ended traceback frames; `--pdb` keeps live frames until the first failure stops execution.
- `--collect-only` lists deterministic test and parameter-case selectors without running bodies. `--fail-fast` stops on failure, error, XPASS, or function teardown failure; SKIP and XFAIL continue. Explicit-worker fail-fast dispatches one case at a time. `--durations N` prints the N slowest completed tests with direct selectors.
- `--json-output` writes one schema-version-1 document for completed runs and CLI errors. It includes framework version, exit code, duration, counts, collection diagnostics, warnings, test output and markers, measurements, complete bounded exception tracebacks, and function/session/run teardown details. `-s --json-output` places uncaptured stdout/stderr, inherited child output, and descriptor writes in `uncaptured_output`. Collect-only uses a versioned `collection` document. `--junit-output PATH` writes a normalized run as JUnit XML; each fixture teardown failure is a separate error case. `--output-schema-versions` lists supported JSON versions.
- The nearest `pyproject.toml` may set `[tool.snektest]` defaults for `test_paths`, `backend` (`"asyncio"` or `"trio"`), `timeout` (a positive number or `false`), `mark`, `capture_output`, `json_output`, and `junit_output`. Explicit filters replace configured paths. Matching CLI flags, including `--no-mark`, `--capture-output`, `--no-json-output`, and `--no-junit-output`, take precedence.
- Console summary lines are compact and may truncate exception details; use full failure details or `--json-output` when exact diagnostics matter.
- `-k EXPR` / `--keyword EXPR` provides pytest-style case-insensitive substring selection. Match function names with bracketed parameter-case IDs, marker names, and project-relative file/directory components. Project root is the nearest `pyproject.toml`, falling back to cwd. Exclude checkout/absolute-parent names; external files expose only their basename. Lowercase operators have precedence `not`, `and`, `or`; parentheses group. Quote the whole expression, e.g. `snektest tests/ -k "fixture and not teardown" --collect-only`. Terms support Unicode word characters and `: + - . [ ] /` and backslash, not regex, globs, quoted terms, or marker arguments. Empty expressions match all; repeated keyword flags fail. `-k slow` matches names and markers, while `--mark slow` matches only markers. Positional selectors validate first; keyword and marker selection then intersect globally, so individual files may have no keyword matches. Empty final selections require `--allow-empty`; missing explicit functions/cases always fail. Order and repeated selections survive. Works locally, with workers, and in collect-only. Syntax errors precede imports, but valid expressions do not skip imports or schema generation. No keyword config default; programmatic runners accept `keyword=`. Keyword-filtered baseline updates preserve unobserved tests.
- Filter runs with paths such as `snektest tests/test_math.py::test_addition` or markers such as `snektest --mark fast`.
- Cancellation arriving during cleanup, including repeated requests, waits for the bounded cleanup phase to finish. Later fixture scopes still receive teardown attempts.
- On asyncio, errors raised while force-closing abandoned tasks are retained as `task_cleanup` background diagnostics for tests, or individual fixture teardown failures. Cleanup continues to other tasks and later tests.
- Broken exception message formatters cannot hide thread or unraisable failures; diagnostics use a safe fallback and still chain the previous hooks.
- Run-fixture setup preserves interruption instead of converting it to a publication error. Established dependencies still tear down. Worker mode stops further dispatch, drains active work, tears down worker sessions and host fixtures, then propagates the interruption.
- Concurrent async run-fixture loads share one in-flight request per identity. Worker connection reads are serialized, and cancelled waiters do not abandon readers: outstanding loads drain before returning a result or receiving another command. Replacement workers receive fresh copies of published descriptors and cached publication failures without repeating host setup. Cross-loading descriptors during batch release preserves existing worker-local copies. Each descriptor restoration uses `--timeout`; `--no-timeout` disables that limit. Failed or timed-out restoration aborts the run with an infrastructure error. Decoder exceptions remain fixture publication failures even when their message formatter raises.
- JUnit replaces XML 1.0-forbidden characters, including NUL and lone surrogates, with U+FFFD in text and attributes. JSON and programmatic results retain the original strings.
- Warning message formatters that raise use a safe text fallback. Capture always restores standard streams and debugger hooks even if exit-time warning rendering fails.
- Measurement contexts are single-use, including after failed entry or exit. Create a fresh `assert_memory` or `assert_benchmark` context for each region. Access and advance `.rounds` only while that context is active; saved iterators cannot resume after exit.
- Async property handoffs copy the sending thread's current ContextVars per example. Hypothesis controls (`note`, `event`, `target`, and `st.data().draw()`) work inside the async example task and directly awaited helpers on either backend, including after suspension. Example/settings/reporter bindings apply only during coroutine steps, not across awaits; shrunk notes remain exception diagnostics. Cancellation keeps the worker context alive through the bounded example-finalization attempt. They do not propagate into separately spawned tasks/threads or async fixture tasks. Sync properties and synchronous schema callbacks retain their native Hypothesis context.
- Cancellation stops new property examples and completes their cross-thread handoffs before bounded cleanup. On asyncio, resistant suspended examples are force-closed under the run cleanup budget, and finalizer errors remain attributed task_cleanup diagnostics. Trio instead aborts before another case if owned work survives that budget. Blocking thread work still needs an outer timeout.
- Owned-task cleanup re-scans cancellation-created descendants under one deadline. Forced finalizers run in their original task context so descendants retain ownership. If tasks survive the final close/reap attempt, the command stops rather than starting another test. Unrelated embedding tasks are left alone.
- CLI runs bound every async test to 60 seconds by default. `snektest --timeout SECONDS` accepts only finite positive values; `snektest --no-timeout` disables the body limit. It is async-only and best-effort: it fires only while the test is suspended on an `await`. Async fixture teardown and task cancellation use the configured timeout, or a separate 60-second cleanup ceiling when the body is unbounded. Local collection and imports have no limit; explicit worker mode bounds child bootstrap but does not promise a per-import deadline. Sync bodies, CPU-bound work, raw threads, and sync teardown cannot be interrupted, even with workers. Hard limits need process-per-case isolation and separately isolated collection, which Snektest does not provide. Put an outer process or CI timeout around every command.
- Timeout interactions: for async `@test_hypothesis`, the timeout bounds the whole property run, not each example. If it fires while an example is suspended, snektest cancels the example and relays that outcome to the Hypothesis worker so the CLI exits promptly. Synchronous or CPU-bound work in that thread remains uninterruptible, and sync property tests are not bounded. Use Hypothesis's own `deadline`/`max_examples` for per-example limits and `--no-timeout` if the complete run must remain unbounded. With `--pdb`, a timed-out test post-mortems on snektest's internal timeout machinery, not the line that hung, so `--pdb` is of limited use for timeouts.
- Every requested filter must select a test by default; empty directories, files, marker selections, and individual filters fail with a collection error. Pass `--allow-empty` only when a zero-test selection is intentional. Explicit missing test-name and parameter-case filters remain errors even in allow-empty mode.
- Install `snektest[schema]` and use `@test_schema("openapi.json", base_url=..., mark="slow")` for positive OpenAPI contract tests. It collects one test per operation and checks for server errors and response-schema violations. Native Schemathesis auth providers and custom checks may be passed through `auth=` and `checks=`. Use `@test_schema_workflow` for linked stateful sequences. Decorated function bodies are declarative and are not called; `base_url` and `headers` may be fixture handles.
- Set `generation="negative"` on `@test_schema` to generate schema-violating requests. A passing response must use an allowed, documented 4xx status; `expected_statuses` defaults to all 4xx responses. Accepted 2xx responses and all 5xx responses fail. Negative stateful workflows are not supported.
Empty parameter-case brackets such as `test_cases[]` are invalid, even with `--allow-empty`, in local, worker, and collect-only modes.

- Recursive directory discovery excludes Git-ignored files; explicitly named test files still run. Outside a Git worktree, every matching `test_*.py` file is checked. Git ignore checks use each symlink's discovery pathname, not its target. Unexpected Git filtering failures inside a worktree are collection errors.

## Live lifecycle progress

Use `--progress-output PATH` with a new path for a version-1 JSONL sidecar, separate
from final stdout JSON and captured output:

```bash
snektest --json-output --progress-output progress-001.jsonl tests/ >results.json
# In another terminal:
tail -f progress-001.jsonl
```

`--progress-interval SECONDS` sets still-running snapshots (default 5 seconds,
finite and at least 0.05). Records cover collection/current file, test start/finish,
managed fixture setup/teardown, and worker incarnation. Canonical selectors retain
occurrence ordinals and a reserved attempt identity. `run_finished` terminates an
ordinary stream; absence means incomplete observation. Worker crashes/coordinator
interruption may leave unmatched child phases even with a terminal event; neither
terminal events nor interruption JSON prove fixture cleanup.

Existing paths are protected; writing uses a companion `.lock` file. Metadata is
bounded and explicitly marks clipped selectors. Active snapshots are bounded;
16 MiB of events triggers an explicit truncation marker, reserving terminal space.
No locals, captured user output, exception messages, or stacks are published.
Names/paths can still contain sensitive user metadata. Still-running snapshots use
an observer thread, not a hard deadline or deadlock detector. An event-loop
heartbeat cannot run while its loop is blocked synchronously; the observer thread
can also lose GIL/CPU access or stall on filesystem IO. Keep an outer timeout.
SIGKILL/hard interruption need not clean up or emit final events/JSON, and arbitrary
subprocess trees are not contained. See `docs/progress.md` for the record contract
and failure/privacy/platform limits; print `--example basic` for the command recipe.
The existing completion reporter interface and JSON result schema version 1 remain
unchanged. No persistent progress configuration is added.

## Collection failures

Default console diagnostics identify the selected file/filter, original exception,
and user source rather than framework plumbing. Syntax carets, causes, notes,
and exception groups remain useful. Rerun the same command with
`--full-traceback` to include framework/dependency frames, including with
`--collect-only` or workers. Both modes are bounded and mark truncation; full
detail is not a locals dump. The flag does not change JSON schema version 1 or
captured collection output/warnings; use `--json-output` for those structured
diagnostics. Collection still exits 2 and finishes before any body runs; fix the
import/declaration instead of weakening validation or adding `--allow-empty`.

## Parameter values and case names

Use `Param(value=..., name=...)`. `Param(value="sqlite")` and `Param("sqlite")`
both lack the required name: write `Param(value="sqlite", name="sqlite")`.
`Param(1, 2, name="pair")` is not a two-value case: write
`Param(value=(1, 2), name="pair")` and accept one tuple argument. Positional
construction with both required fields remains supported.

```python
from snektest import Param, assert_eq, test


@test(
    [
        Param(value=(2, 3, 5), name="small"),
        Param(value=(100, 200, 300), name="large"),
    ],
    mark="fast",
)
def test_tuple_value(case: tuple[int, int, int]) -> None:
    # One Param passes one tuple argument, not three separate arguments.
    left, right, expected = case
    assert_eq(left + right, expected)
```

A tuple-valued list selects related inputs as one argument; two parameter lists
supply two arguments and form a Cartesian product, not pairwise zip. Each list
must be non-empty; names must be non-empty, unique within that list, and exclude
`, `, `[` and `]`. Explicit stable names become selector IDs; never infer them
from arbitrary value reprs. One decorated test may expand to at most 10,000 cases;
split larger matrices or sample with Hypothesis. `@test` checks parameter types
through two lists; three or more use its variadic `Any` fallback.

Print the complete scalar/tuple/two-axis example with
`snektest --example parametrize > test_parametrize.py`, then inspect selectors:

```bash
snektest test_parametrize.py --collect-only
snektest 'test_parametrize.py::test_tuple_value[small]'
snektest 'test_parametrize.py::test_cartesian_product[hello, world]'
```

An unconstrained axis may widen literal values to `str`. When the test accepts
`Literal` values, preserve them by annotating the axis as `list[Param[Backend]]`
(or use explicit `Param[Backend](...)` constructors); avoid casts or `Any`:

```python
from typing import Literal

from snektest import Param, assert_in, test


type Backend = Literal["sqlite", "mariadb"]

BACKENDS: list[Param[Backend]] = [
    Param(value="sqlite", name="sqlite"),
    Param(value="mariadb", name="mariadb"),
]


@test(BACKENDS, mark="fast")
def test_literal_backend(backend: Backend) -> None:
    assert_in(backend, ("sqlite", "mariadb"))
```

## Narrowing and assertion argument order

Bind narrowing helpers' return values when using the checked value later. This
both asserts and gives the checker a non-`None` or instance-specific value;
calling a helper without binding its result is not the recommended narrowing
pattern. Discard with `_ = assert_isinstance(value, SomeType)` for a pure check.
Keep the observed/computed value first in subsequent comparisons:

```python
from snektest import assert_eq, assert_is_not_none, assert_isinstance, test


@test(mark="fast")
def test_bound_narrowing_helpers() -> None:
    settings: dict[str, object] = {"suite": "SNEKTEST"}
    configured = assert_is_not_none(settings.get("suite"))
    name = assert_isinstance(configured, str)
    assert_eq(name.lower(), "snektest")
```

## Exception text and original causes

Use `match=` for regex search, or capture `.exception` and assert its exact text.
`msg=` only overrides a failing assertion's diagnostic: it does not match text.
Matching never searches causes or notes. Inspect the original cause explicitly:

```python
from snektest import assert_eq, assert_in, assert_isinstance, assert_raises, test


def _parse_port(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        message = "invalid port"
        raise RuntimeError(message) from error


@test(mark="fast")
def test_exception_cause() -> None:
    with assert_raises(RuntimeError, msg="port conversion should fail") as caught:
        _ = _parse_port("not-a-number")

    assert_eq(str(caught.exception), "invalid port")
    cause = assert_isinstance(caught.exception.__cause__, ValueError)
    assert_in("not-a-number", str(cause))
```

The deliberately different `msg=` does not prevent this test from passing.
Print the complete assertion examples with `snektest --example basic`.

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

## GraphQL contracts

Install `snektest[schema]` and pass local SDL or introspection JSON to
`@test_graphql`.
It collects one case per query root field, such as
`test_graphql_contract[Query.greeting]`. Collection does not contact the endpoint.
Use a disposable service: even queries can have application-specific side effects.

For introspection, pass the saved JSON path instead of the SDL path, for example
`test_graphql("introspection.json", url="http://127.0.0.1:8000/graphql")`.
Both raw `{"__schema": ...}` exports and full `{"data": {"__schema": ...}}`
responses are accepted, including UTF-8 files with a BOM. JSON is recognized by
content; a `.json` suffix requires valid JSON. Responses containing non-empty
`errors` are rejected even when they also contain schema data. Export a successful
introspection response first; Snektest does not perform live introspection.

<!-- snektest-doc: skip-run -->
```python
from hypothesis import settings

from snektest import test_graphql


@settings(max_examples=50, deadline=None)
@test_graphql(
    "schema.graphql",
    url="http://127.0.0.1:8000/graphql",
    request_timeout=5.0,
    mark="slow",
)
async def test_graphql_contract() -> None:
    ...
```

The body is metadata-only and is not called. `url` is the complete HTTP endpoint,
including its path, and accepts a literal or sync/async fixture handle.
`headers` likewise accepts a dictionary or sync/async fixture handle, resolved
before generation starts. Pass a native Schemathesis auth provider class through
`auth=` for cached login or token refresh. `checks=` accepts native Schemathesis
checks in addition to the built-in checks; raise `AssertionError` for a contract
violation. Other check exceptions remain errors. Auth providers and checks run
in the Hypothesis worker thread, so resolve async resources through fixtures first.
Hypothesis generates positive operations and shrinks failures. HTTP server errors,
malformed JSON, non-object responses, and non-empty GraphQL `errors` fail,
including HTTP 200 and partial-data responses. Transport and configuration
exceptions remain errors. Failure messages include the root field, HTTP server-error status when applicable,
and generated query. Generated values and server error messages may contain sensitive data;
review diagnostics before sharing them.

Queries are selected by default. Mutations require `allow_mutations=True`,
which permits both kinds before filtering. A mutation filter alone never enables
writes. Use a disposable service: generated mutations may delete or modify data,
and Hypothesis can repeat requests while shrinking a failure.

Pass `operations=GraphQLFilter(...)` to select exact root fields and kinds.
Each `GraphQLOperationSelector` combines `kind` and `field` with AND; the include
and exclude tuples are OR sets, and excludes win. An empty include tuple means
all eligible fields. Names are case-sensitive GraphQL field names, not dotted
labels or patterns. Kinds refer to query/mutation semantics even with custom
root type names. Selection preserves schema order and rejects empty results.

For example, opt into one mutation explicitly:

<!-- snektest-doc: skip-run -->
```python
from snektest import GraphQLFilter, GraphQLOperationSelector, test_graphql


@test_graphql(
    "schema.graphql",
    url="http://127.0.0.1:8000/graphql",
    allow_mutations=True,
    operations=GraphQLFilter(
        include=(GraphQLOperationSelector(kind="mutation", field="createWidget"),),
    ),
    mark="slow",
)
async def test_create_widget_contract() -> None:
    ...
```

With a default root type, this collects
`test_create_widget_contract[Mutation.createWidget]`, independently selectable
on the CLI. Subscriptions and stateful sequences remain unsupported. The adapter
does not comprehensively validate returned field types against SDL; use ordinary
Snektest tests for application-specific response assertions.
Local and worker execution use the existing console, JSON, and JUnit reporters.

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

## Managed temporary directories

Call `temporary_directory(base=..., retention="never" | "on-failure" | "always")`
inside sync/async tests or fixture setup; it returns `Path` immediately, not a
fixture handle. Default "never" cleans even failed tests. "on-failure" uses final
outcomes including teardown/task/background failures, not exceptions consumed by
`assert_raises`, SKIP or XFAIL. "always" retains successful evidence too.

```python
from snektest import assert_eq, temporary_directory, test


@test(mark="medium")
def test_owned_file() -> None:
    root = temporary_directory(retention="on-failure")
    target = root / "payload.txt"
    _ = target.write_text("payload", encoding="utf-8")
    assert_eq(target.read_text(encoding="utf-8"), "payload")
```

Ordinary resource teardown and task cleanup precede directory policy. Session setup
infers session ownership: roots survive between cases; retention considers any
failure in that execution process and its session teardown, not other workers or
later run-fixture errors. Every call is fresh; use a cached session fixture to
share one path. Run fixtures/standalone context-manager use are unsupported.

Supply an existing short private `base` to avoid tmpfs quotas/socket limits; pass
paths or child environments explicitly and stop/reap consumers first. Never infer
ownership of library-created paths. Global TMPDIR and reusable data are unchanged.
Completed console/JSON results expose retained paths, not contents; the run's
`retained_directories` aggregates individual-test and session records. Removal
errors are fixture errors without replacing the test's failure. Large removal
runs in a thread and waits through caller cancellation, but threads cannot be
interrupted: a filesystem hang needs an outer timeout. Crashes/SIGKILL can orphan
roots, interruptions may prevent the final path report, and intentional retention
still accumulates. No automatic pruning is offered. Print `--example temporary`.

## Explicit log capture

Use `with capture_logs(logger, level=logging.DEBUG) as logs` inside either sync
or async tests. Import `capture_logs` and `LogCapture` from `snektest`; pass a
logger name or `logging.Logger` explicitly (`""` selects root). `logs.records` is
an immutable tuple snapshot of original mutable `logging.LogRecord` objects,
including structured attributes; `logs.messages` renders a fresh tuple snapshot
on access. Rendering errors become `<log message unavailable>` without exception
text/arguments; real interruptions propagate.

Capture includes matching emissions from all threads in the current process,
not task-local or cross-process records. Nested/overlapping scopes preserve
independent thresholds and restore temporary levels even when exiting out of
order. Unchanged levels are not reconfigured. If an application setter fails
restoration, propagate the error but retire capture ownership and refresh other
active scopes. Existing handlers remain active; lowering a logger threshold can enable
additional application-handler output. Respect descendant levels, filters,
propagation, disabled source loggers, and global logging disable. `NOTSET` captures
already-enabled emissions without lowering the threshold. Levels are non-negative
integers, excluding bool. Contexts are single-use. Coordinate external logger
reconfiguration while captures are active.

Place capture inside a fixture around its `yield` to include teardown; a body
context exits before function fixtures tear down. Records may contain secrets;
there is no automatic result attachment or redaction. Application-handler output
still follows normal stdout/stderr capture. See `snektest --example logging`.

## Copyable examples

List bundled examples:

```bash
snektest --examples
```

Print one example:

```bash
snektest --example basic
snektest --example fixtures
snektest --example temporary
snektest --example async
snektest --example benchmark
snektest --example memory
snektest --example logging
snektest --example typing
snektest --example outcomes
snektest --example parametrize
snektest --example schema
snektest --example graphql
```
"""

EXAMPLE_FILES: dict[str, str] = {
    "anyio": "anyio_tests.py",
    "async": "async_tests.py",
    "basic": "basic_test.py",
    "benchmark": "benchmark.py",
    "fixtures": "fixtures.py",
    "temporary": "temporary.py",
    "graphql": "graphql.py",
    "logging": "log_capture.py",
    "memory": "memory.py",
    "outcomes": "outcomes.py",
    "parametrize": "parametrize.py",
    "schema": "schema.py",
    "typing": "type_contracts.py",
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
