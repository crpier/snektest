# Changelog

Notable user-facing changes are recorded here.

## Unreleased

- Begin Python-native static typing contracts with bare `@test_typing`, native `typing.assert_type`, and `with assert_type_error("rule")` blocks. Analyze original sync/async source without executing bodies, retain normal selectors and results, and provide the optional pinned `snektest[typing]` extra. Harden body-only diagnostic attribution, source identity/edit detection, lexical marker scopes, and original-location failure messages. Validate installed optional-extra boundaries, cancellation during native startup, oversized-report cleanup, fail-fast/JUnit results, and replacement workers. One selected function is still checked per invocation; broader lifecycle validation, batching, and configurable environments remain in progress. (#96)

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
