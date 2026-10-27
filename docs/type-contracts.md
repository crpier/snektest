# Python-native typing contracts

`@test_typing` registers a module-level sync/async Python function as a static
contract. Bodies never execute; typed parameters are symbolic inputs. Use native
`typing.assert_type` for positives and a literal
`with assert_type_error("ty-rule"):` block for exactly one rejection. Only a
single bare decorator is supported. Imported aliases and qualified module names,
including imports inside static functions, preserve Python lexical scope. Both
`import snektest as s` and direct aliased/dotted `snektest.type_contracts` imports
are recognized, including `from snektest import type_contracts as contracts`;
the canonical user-facing import style remains top-level.

## Checker view and assertion declaration validation

Ordinary consumers see `test_typing` as `typing.no_type_check` through both
package and implementation-module stubs. The temporary view is derived from those
same stubs, replacing only the decorator and adding the private marker probe.
Installed wheel/sdist acceptance proves ordinary checks skip positive-only errors
while Snektest still detects them through direct module imports. A selected static
case uses a temporary partial stub to restore an identity decorator while ty
analyzes unchanged original source. Source fingerprints, code name/first line,
and body-only Unicode coordinates preserve declaration and diagnostic identity.
These checks detect edits, not atomic protection against concurrent writers.

The checker-only helper stub additionally uses a PEP 702 deprecated-reference
probe with a reserved internal message. This is **not** a runtime or public stub
deprecation. Ty resolves the real helper identity through copied/re-exported
bindings; probe references must belong to a permitted literal import or a
recognized assertion header. Unsupported known helper escapes become declaration
errors instead of silently disappearing. Probe diagnostics are consumed before
matching user diagnostics, including a user's ordinary `deprecated` rejection.
Direct dynamic marker lookups are also rejected. Arbitrary opaque Python code
is not interpreted to infer intended assertions: only the documented literal
form declares one; unrelated context managers remain ordinary type checks.

## Explicit configuration

`--typing-config FILE`, or `typing_config=Path(...)` on the programmatic runner,
selects native ty TOML configuration. The default is isolated `[rules] all =
"error"`, independent of ambient project config and `TY_CONFIG_FILE`. The current
Python interpreter is the default; native `environment.python` explicitly selects
another interpreter/environment and `environment.extra-paths` selects additional
import roots. Snektest only inspects whether the explicit Python field exists to
avoid overriding it with its default; native parsing/type validation still owns
configuration errors. An analysis environment does not expand Snektest's supported
runtime matrix. Only the framework package is staged alongside its checker stub;
its host's whole site-packages is never an implicit import root. Selected
environments cannot resolve host-only dependencies or inherit host versions this
way. Non-standard runtime sys.path additions require matching native extra-paths,
just as they do with an ordinary explicit ty invocation.
The `deprecated`, `type-assertion-failure`,
and `unresolved-import` rules remain errors so configuration cannot suppress
reference validation, positive type assertions, or broken environments. Native
per-file overrides take precedence over CLI severities, so every override must
preserve these three rules at `error`. An override that lowers one directly or
through `all` is a configuration ERROR, even if its pattern does not match this
selection. A per-file `all = "ignore"` is supported when the same override sets
all three protected rules explicitly to `error`; other rules may be relaxed.
Native syntax/type errors still receive native diagnostics. If native parsing
ever accepts syntax that the framework cannot safely inspect, analysis fails
closed rather than certifying the unchecked configuration. Ignoring
an expected rejection rule cannot satisfy that assertion: the missing diagnostic
still fails. The option is not a persistent `[tool.snektest]` key.

Configuration scope is run-local and propagated to original/replacement workers.
Content fingerprints invalidate report reuse when explicit configuration changes;
an edit during analysis is an error. Native parsing/startup failures are framework
errors. Runtime-only and collect-only runs do not start ty or parse native config.

## Bounded reuse and cleanup

An internal session fixture owns one most-recent validated file report per
execution process. Repeated same-source cases reuse it without eagerly validating
later assertion declarations. The key includes source and configuration content.
Switching files can evict the report; later in-process runs and replacement
workers start fresh. Keep source and dependency files stable for the whole run.
This is bounded per-process reuse, not a global whole-suite daemon or scheduler
affinity guarantee. Failed invocations/cancellation do not populate the cache.

Stdout/stderr each have a 4 MiB limit. Cancellation and report-limit failure kill
and reap the checker and **drain both pipes** under the cleanup budget. A process
exit alone is insufficient: a paused asyncio transport can retain an open pipe
once its original reader is cancelled. Discarded chunks remain bounded in size.
Async test timeouts are best-effort; synchronous collection, imports, and local
source parsing still require an external whole-command timeout.

## Evidence and limits

Canonical acceptance uses real ty, local/worker runners, fail-fast/JUnit,
replacement workers, and independently installed wheel/sdist environments both
without and with the typing extra. Disposable embedding applications observe real
subprocess audit events; they do not patch the checker or emit fake reports.
Linux-specific procfs/pipe controls verify active timeout/cancellation/termination
reaping and descriptor release under full native-report backpressure. Transport
corruption consumes a byte from real native output (without a fake checker or
synthesized report), proving malformed JSON is an error rather than a rejection.
Owned installed environments additionally verify non-executable checker startup;
permission changes break uv-cache hardlinks first and never touch shared binaries.
Those OS fixtures skip where their required facilities or pipe-growth permission are
unavailable; they do not establish equivalent macOS/Windows fault coverage.

PR #116 records exact commits, gate counts/coverage, representative downstream
acceptance, and remaining review work. This is representative downstream
acceptance, not a full migration or proof of every possible protocol fault.
