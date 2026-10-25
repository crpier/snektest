# Explicit lazy imports (#93)

## Policy

Every eligible import in runtime `.py` files and bundled examples uses Python
3.15's explicit `lazy` syntax. Future imports, stubs, and syntax-restricted
contexts are exceptions. Embedded documentation continues to show ordinary
consumer imports; callers need not adopt lazy syntax to use snektest.

This is a source convention, not a promise that every import stays unresolved:
base classes, decorators, defaults, and module-level objects can immediately
resolve a name. Ruff TID255 conflicts with that convention and is intentionally
ignored; ty's strict rules remain enabled. An architecture regression checks
eligible imports without interpreting documentation strings as code.

The public facade defers execution modules and engines. Ordinary `@test`
decorators do not resolve `hypothesis.given`; that happens when a property run
actually needs it. CLI help/version likewise avoid the property/schema engines.

## Initialization ownership

`import_policy.py` owns one synchronous, reentrant, serialized initialization
scope. It composes with `sys.get_lazy_imports_filter()` and restores that exact
filter in `finally`; `sys.get_lazy_imports()` is never changed. Never hold this
scope across an await or a fixture/test lifetime.

Within the scope, imports originating in these namespaces are eager:

- The isolated synthetic test-package namespace, so selected module and package
  imports, including explicitly lazy imports, finish during collection.
- Hypothesis, Schemathesis, GraphQL, hypothesis-graphql, and hypothesis-jsonschema,
  whose registration/re-export behavior needs eager internal imports.

The scope is active while importing collected modules and while synchronously
constructing schema decorators. The latter also supports schema factories called
from ordinary test bodies after collection. Imports in unrelated namespaces
continue through the caller's existing filter. This does not recursively realize
all lazy bindings in application libraries, change function-local import timing,
or establish a lasting process-wide dependency exclusion.

The locked Hypothesis strategy export check fails with unresolved strategy
registrations under unfiltered forced-lazy mode. GraphQL also confuses lazy
re-exports with same-named modules. These are reproduced by the integration
suite and handled at framework-owned initialization, rather than requiring a
Hypothesis exclusion in the downstream launch wrapper.

Standalone Hypothesis or unrelated libraries imported outside snektest's
initialization scopes still belong to the embedding application. In particular,
this policy does not take ownership of OpenTelemetry SDK initialization.

## Verification

The release gate runs the canonical suite under coverage in normal mode, checks
its unchanged 90% core threshold, then reruns the suite with
`python -X lazy_imports=all -m snektest tests`, with
`PYTHON_LAZY_IMPORTS=all` inherited by test subprocesses. CI keeps the Python 3.15
Linux, macOS, and Windows matrix and its 15-minute outer timeout.

Consumer subprocess regressions cover package/decorator/help/version deferral,
unused missing lazy imports becoming collection errors, relative helper output
and warnings, caller-filter delegation/restoration after success and failure,
and sync/async property tests using run fixtures in local and spawned-worker
modes. Both independently installed wheel and source archives also run a
property test with a run fixture in a spawned worker in both interpreter modes.
The memory regression keeps the existing 8 MiB warmed-run limit and separately
bounds cold initialization plus the same 1,000 cases at 16 MiB; see
[large-suite memory](large-suite-memory.md). Existing collection, optional-dependency,
schema, worker-replacement, and fixture publication regressions run unchanged in
the full suites.

## Local results

The complete Linux release gate passed:

- **910 tests** under normal-mode coverage; **94%** core coverage.
- **910 tests** under forced-lazy mode, including inherited subprocess mode.
- Locked dependency audit, Ruff formatting/lint, strict ty, wheel/sdist manifests,
  and independently installed archive smoke tests passed.
- A separate fresh production-only environment installed each archive in turn.
  Without Schemathesis installed, `test_schema` reported the `snektest[schema]`
  installation hint and restored the caller's filter in both modes.

Cross-platform verification requires CI. These Linux results do not establish a
macOS or Windows result.

## Downstream validation

Validated `fifth-snekql` at `395f731c58916109a21b606777fb1729543e92a8`
using its locked dependencies in an isolated environment, with the working
snektest checkout and `PYTHON_CONTEXT_AWARE_WARNINGS=1`. No downstream tracked
files or existing environment were changed.

The direct invocation selected `tests/query`,
`tests/test_identifier_quoting_properties.py`, and
`tests/test_schema_compile_properties.py`:

- Normal mode: **680 passed**.
- Forced-lazy mode: **680 passed**, without a Hypothesis exclusion or launch
  wrapper.

OpenTelemetry remains application-owned. Direct forced-lazy collection of
`tests/runtime/test_opentelemetry.py` failed on the SDK's circular import of
`Sum` from a partially initialized `opentelemetry.sdk.metrics._internal.point`.
A temporary application filter excluding **only** `opentelemetry.sdk` and its
submodules collected all **19 selectors** successfully. This is collection-only
validation, not a green execution claim or a reason to weaken thread hygiene.

This is targeted downstream validation, not a full acceptance run. The empty
collation case ID, reproduced AnyIO/OpenTelemetry thread leaks, and MariaDB
resource limitations from the earlier broader run remain documented in
[the Python 3.15 migration report](python315-migration.md).
