# Changelog

Notable user-facing changes are recorded here.

## Unreleased

- Add explicit command-wide `--backend asyncio|trio`, project `backend`, and programmatic selection with optional `snektest[trio]`. Preserve asyncio by default; use native Trio for fixture/task lifetimes, original/replacement workers, Hypothesis/schema handoffs, ty subprocesses, measurements, and progress. Reject unavailable or mismatched runtimes without fallback. Trio cleanup is cooperative and fail-stops before another case if owned work survives its budget; shielding/shutdown still require an outer deadline. (#100)

- Give every managed async fixture one owning task for setup, yield, and teardown, allowing AnyIO scopes and async services to span yield in all fixture scopes. Keep the run-fixture host loop live between requests, retain warning diagnostics and unfinished-setup cleanup, report malformed yields without losing structured output, and reject function loads after teardown starts. Fixture ContextVar changes are now isolated from tests; pass context/resources through yielded values. Sync loading is unchanged. (#100)

- Begin optional Python-native typing contracts with bare `@test_typing`, native
  `typing.assert_type`, and exact `assert_type_error("rule")` blocks. Analyze
  original sync/async source without executing bodies; preserve selectors and
  normalized results. Both package/module stubs skip bodies during ordinary ty
  checks, so run Snektest to validate contracts. (#96)
- Reuse one bounded file report per run/worker and accept explicit native
  `--typing-config FILE` / programmatic configuration, including Python environments
  and extra import roots. Isolate target dependencies from host-only packages and
  versions; preserve source locations, lexical assertion identity, and exact
  diagnostic ownership. Invalid declarations, source edits, missing dependencies,
  and checker failures cannot certify expected rejections. Reject per-file
  overrides that weaken protected diagnostics instead of silently passing
  positive-only contracts. (#96)
- Bound native typing reports and drain/reap checker processes on timeout,
  cancellation, and output-limit failures. Add real process/pipe, malformed-report,
  installed-extra, environment-isolation, and qualified-import acceptance without
  fake checkers. Hard collection/synchronous deadlines still require an external
  command timeout; OS-specific fault evidence remains qualified. (#96)

- Recognize idle AnyIO asyncio pool workers by current-loop registration and identity, without importing AnyIO, prewarming/shutting down pools, or exempting thread names or newly created busy workers. Add the optional `snektest[anyio]` extra, executable examples, and explicit task-local fixture-scope/backend limitations. (#100)

- Add typed `temporary_directory(base=..., retention=...)` with inferred test/session ownership, default cleanup even on failure, explicit on-failure/always retention, off-loop recursive removal, ownership checks, and path-only console/programmatic/JSON reporting. Keep resource teardown before deletion and cleanup errors alongside original failures. Session policy is process-local; no global TMPDIR mutation, process management, historical pruning or hard-kill cleanup guarantees. (#97)

- Make console collection errors user-first, with selected file/filter context, original types, source/carets, causes, notes, groups, and corrective `Param` guidance. Add `--full-traceback` for bounded framework/dependency details in local and worker modes; preserve JSON schema/output and collection semantics. Broken exception message formatters use a safe fallback instead of hiding the original collection error. (#102)

- Add opt-in `--progress-output PATH` lifecycle JSONL sidecars and `--progress-interval` still-running snapshots without changing stdout JSON or completion reporters. Observe collection/files, test occurrences, fixture setup/teardown, and worker incarnations; preserve partial evidence across interruption/crashes with explicit termination, privacy, metadata and output limits. (#101)
- Define the POSIX CLI signal contract: preserve inherited ignores rather than reset background-job policy; document custom-handler exec/spawn boundaries, a SIGINT-only background launcher, application-child ownership, and coordinator-only worker interruption limits. Add real shell/local/worker/subprocess and embedding coverage. (#108)
- Enable context-aware warnings by default at standalone CLI startup, including spawned/replacement workers. Honor explicit Python startup opt-outs and precedence; preserve interpreter options, venv, argument boundaries, streams and exits across relaunch, while leaving library/programmatic policy unchanged. Interpreter/site startup hooks may repeat. (#107)

- Clarify canonical named `Param` construction, tuple values versus Cartesian axes, stable selectors, and preserved `Literal` types. Expand executable assertion guidance for exact/regex exception text, `msg` overrides, original causes, actual-first comparisons, and bound narrowing results; make the embedded guide and copyable examples part of the recommended authoring workflow. Existing constructor and assertion behavior is unchanged. (#104)

- Add `assert_raises(match=...)` for regex search of exception messages, supporting strings and compiled string patterns while preserving typed exception capture. Message mismatches and broken formatters are assertion failures with the original cause; invalid regexes and bytes patterns are rejected before entering the context body. `msg=` remains a failure-message override. (#105)

- Add typed, explicit `capture_logs` / `LogCapture` for stdlib logger hierarchies, original structured records and safely rendered message snapshots, sync/async fixture lifetimes, overlapping scopes, and thread-originated records. Preserve application handlers, avoid unnecessary level writes, and retire capture ownership safely if application level setters fail, without automatic result attachments. (#95)

- Correct the invalid test filename diagnostic to identify the required `test_` prefix instead of `_test`, without changing filename validation rules. (#103)

- Explain `assert_eq` failures with bounded character context for long strings, expected-to-actual unified multiline diffs, and dictionary/sequence key/index paths. Preserve comparison semantics and custom messages; avoid user representation and extra equality hooks during diagnostics, and share safe messages across console, workers, and existing JSON version-1 output. (#94)

- Adopt explicit lazy imports throughout the runtime and bundled examples. Package imports, ordinary decorators, and CLI help/version defer property/schema engines. Preserve eager collection of test-module imports and scoped engine registration under forced-lazy mode without changing the caller's interpreter mode or retaining an import filter. The release gate now runs the complete suite in both modes. (#93)

- Require Python 3.15 or newer and drop Python 3.14 support. Move the Linux, macOS, and Windows release gate to GIL-enabled CPython 3.15, require Pydantic 2.14 for its Python 3.15 support, and update the pinned uv and validation toolchain. Existing virtual environments must be recreated with Python 3.15 before upgrading. (#92)

- Add pytest-style `-k EXPR` / `--keyword EXPR` selection for names, parameter cases, and markers, with case-insensitive substrings and boolean expressions. Selection works globally across positional paths in local, worker, and collect-only modes. Keyword-filtered benchmark updates preserve unobserved tests.

- Preserve worker-local run-fixture copies when cross-loads race a publication batch's release, rather than crashing on a repeated commit.

- Bound run-fixture descriptor restoration in replacement workers with the configured timeout and stop failed replacements before dispatch. Decoder exceptions with broken message formatters remain fixture publication failures rather than corrupting the worker protocol.

- Retain task ownership when fixture setup fails before yielding. Scope teardown reports abandoned tasks and finalizer failures without replacing the setup error; session retries preserve cleanup for every attempt. Pending shared async session setup also survives cancellation of a test's waiter across tests. Bound cancellation of unfinished setup at scope teardown and retain its cleanup errors as fixture diagnostics. Function setup left pending when its test returns is reported as abandoned fixture work and cleaned before the next test.

- Add `test_graphql` contracts from local SDL or raw/data-wrapped introspection JSON through `snektest[schema]`, with fixture-backed endpoints and headers, native auth providers and custom checks, root-field filters, explicit mutation opt-in, and root-field-attributed generated failures with HTTP server-error status codes.

- Reap cancellation-created task descendants under one cleanup deadline, preserve ownership during forced finalization, and stop unsafe continuation if owned tasks survive. (#74)

- Bound cancellation-resistant async Hypothesis cleanup, stop new examples, release worker handoffs, and retain finalizer diagnostics. (#73)

- Make memory and benchmark contexts single-use; reject rounds access or resumption outside their active lifetime instead of publishing stale samples. (#72)

- Restore capture state unconditionally and retain safe warning text when exit-time message formatting raises. (#71)

- Replace XML-forbidden characters in JUnit reports with U+FFFD without changing JSON or programmatic strings. (#70)

- Serialize worker run-fixture readers, share concurrent first loads, and drain pending readers after waiter cancellation. (#69)

- Preserve run-fixture setup interruption and exit codes after dependency cleanup; workers stop new dispatch and tear down before propagating host interruption. (#68)

- Preserve thread and unraisable failures when an exception message formatter raises. (#67)

- Contain forced task-finalizer errors, retain attributed diagnostics, and finish reaping tasks without aborting later selected tests. (#66)

- Finish bounded fixture and task cleanup before propagating cancellation received during teardown, including repeated requests and later fixture scopes. (#65)

- Reject empty parameter-case brackets consistently before local or worker execution, including collect-only and `--allow-empty`. (#64)

- Fixed Git ignore filtering for symlink discovery paths; failed Git batches now report collection errors inside a worktree. (#63)

- Added deterministic, import-safe collection and strict empty-selection checks.
- Added bounded async cleanup, async-test timeouts, and task ownership diagnostics.
- Added process workers, run fixtures, memory assertions, and benchmark baselines.
- Added a deliberate public API and background-thread failure diagnostics.
- Added one cross-platform release gate and trusted, attested PyPI publishing.
- Rejected non-finite timeouts and documented the verified runtime and hard-timeout boundaries.
- Added reasoned SKIP, XFAIL, and strict XPASS outcomes across console, JSON, and programmatic runs.
- Added a versioned, machine-safe JSON contract, normalized run results, and JUnit XML output.
- Added collect-only, fail-fast, slowest-duration selectors, project defaults, schema-version discovery, and 0.17 migration guidance.
- Capped each parameterized test at 10,000 Cartesian cases, removed the serial callback queue, bounded worker run-ahead, released unused passing output and traceback locals, and added large-suite memory budgets. (#38)
- Stabilized the Windows release gate by allowing nested CLI processes enough startup time under coverage. (#59)

## 0.16.0

Earlier development release.
