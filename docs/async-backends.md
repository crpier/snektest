# Native async backends and AnyIO (#100)

## Explicit command-wide choice

Asyncio remains the default and needs no optional engine. Install
`snektest[trio]` for native Trio `>=0.34,<0.35`, then select
`--backend trio`. `--backend asyncio` explicitly selects the default. The nearest
`pyproject.toml` may set `[tool.snektest] backend = "trio"`; a CLI choice overrides
that default. Unknown names, repeated flags, unavailable engines, and invalid
configuration fail before selected modules are imported. Informational commands
need neither project configuration nor an optional engine.

The choice covers local execution, collection/run-fixture host, original workers,
replacement workers, fixture/task ownership, deadlines, and thread handoffs.
There is no per-test backend decorator or automatic fallback. Asyncio-specific
APIs still require asyncio; selecting Trio does not translate arbitrary asyncio
libraries. Existing normalized result/status types and JSON schema version 1 are
unchanged; progress remains an independent opt-in sidecar.

Embedding applications pass `backend="trio"` to
`snektest.cli.run_tests_programmatic` while running their own native Trio or
AnyIO-with-Trio runtime. Omitting it still means asyncio, not host autodetection.
The selected engine must match the caller's running runtime. Snektest neither
replaces that runtime nor nests another event loop. Startup configuration IO and
standalone assertion suspension probes can observe the actual host without
changing the explicit execution choice.

`runtime.py` owns engine validation, standalone entry, context-copying offloads,
deadlines, and startup adapters. `runtime_tasks.py` owns managed tasks, result
cells, events, shielded completion, native task inventory, and waits. Core paths
do not import Trio or AnyIO merely because those packages are installed.

## AnyIO and task-affine fixtures

Install `snektest[anyio]` for AnyIO `>=4.14.2,<5`, or
`snektest[anyio,trio]` to use its Trio backend. Await AnyIO APIs directly in tests
and fixtures; no nested `anyio.run()` is needed. Print `--example anyio` for a
portable fixture-backed service, runnable with either selected backend.

Each managed async generator has one persistent owning task for setup, yield,
and teardown in every scope. AnyIO/Trio cancel scopes, nurseries, task groups,
and library context managers may span yield. Each dependency has its own task
and scope stack. The fixture host keeps its native loop live while waiting for
commands, so run-fixture services remain responsive between requests. Trio
teardown deadlines live on the managed task's **outer** cancel scope; inserting
another scope beneath a suspended user scope would violate native LIFO rules.

The task inherits its loader's context; ContextVar changes remain fixture-local.
Yield resources/context explicitly. Scopes surrounding yield govern fixture work
and its descendants, not the test body. Protect setup/yield with try/finally and
stop forever-running children during teardown; ordinary nursery/task-group exit
waits for them. Sync fixtures remain synchronous; runtime scopes across yield
require async fixtures. Standalone handle context managers run in their caller's
task rather than using this registry-owned driver.

Shared cached setup survives a cancelled waiter. Function setup follows its
waiter's cancellation. Every attempted setup retains cleanup ownership, including
before its task first runs. Function loads after teardown starts remain rejected.
Dependency ordering, descriptor-copy publication, and warning attribution stay
unchanged. Setup warnings reach the waiter; later fixture-background warnings
join teardown diagnostics. Explicit context-aware-warning opt-outs retain
phase-local capture rather than lifetime-spanning global catchers.

## Cancellation and fail-stop boundaries

Deadlines translate only their own cancellation into stdlib `TimeoutError`;
application-raised timeouts and parent cancellation retain their identities.
Body timeouts are cooperative and async-only. Trio shielding can postpone a
body timeout; blocking imports, sync/CPU-bound bodies, and sync teardown still
require an outer process/CI deadline.

Asyncio retains its existing budgeted cleanup and resistant-coroutine closure.
**Trio never force-closes a coroutine from another task or mutates task
internals.** Managed task cancellation/fixture cleanup gets the configured
budget (60 seconds when body limits are disabled). If owned work remains alive,
`RunInfrastructureError` aborts before another selected case starts. Native
unmanaged descendants carrying the owner context have no safe public per-task
cancel operation; the runner inventories them without cancelling a nursery that might also own unrelated
embedding work. Such work must finish within the cleanup budget or fail-stop.
Native runtime shutdown can still wait on shielded work and require the external
deadline. Worker infrastructure failures are announced before native shutdown,
so the coordinator can stop dispatch and retire children; forced retirement does
not certify graceful fixture teardown.

Real probes showed that cross-task `coro.close()` corrupts Trio cancel-scope
stacks and that shielded children can block nursery shutdown. The backend-specific
contract is deliberate, not an equivalent hard-termination claim. See
[the hard-timeout boundary](adr/0001-hard-timeout-boundary.md).

Hypothesis handoffs retain/drain their thread completion and owned examples,
and copy each sender's current ContextVars rather than a frozen entry context.
`hypothesis_context.py` bridges Hypothesis's thread-local build context, settings
and reporter for the example task and directly awaited helpers on either backend.
It binds the worker's existing values around each iterator send/throw/close and
restores the loop thread's prior values before yielding any native suspension
instruction. `note`, `event`, `target` and `st.data().draw()` therefore work after
awaits, with final notes/draws reaching the shrunk exception rather than stdout.
Re-entering BuildContext would run cleanup twice; binding across an await would
contaminate unrelated embedding tasks. Neither is permitted. Native task identity,
ContextVars, scope LIFO, cancellation and force-close diagnostics stay with the
original runtime. Separate tasks/threads and async fixtures do not inherit this
step-bound Hypothesis context. The three private DynamicVariable dependencies
are isolated in this module; copying ContextVars alone cannot bridge them.
Cancellation releases handoffs immediately but keeps the worker's build context
alive until the bounded example-cleanup attempt finishes, so async finalizers can
still draw inputs/register cleanup. Retirement after a failed budget unblocks the
worker without certifying cleanup; drain failures preserve infrastructure errors.
Synchronous properties/schema callbacks retain their native Hypothesis context.
Worker publication readers likewise drain before another command can receive
from their connection. Native ty subprocess cancellation kills/reaps the checker,
drains both bounded report streams, and closes Trio's pipe streams. Memory
assertions observe native task suspension without retaining task references
after the measurement exits. Progress classifies native cancellation without
publishing exception messages or task frames.

## Thread observability

AnyIO's asyncio pool retains non-daemon idle workers. The existing private RunVar
adapter exempts only identities registered as idle on the current asyncio loop;
unknown registry shapes fail closed. It reads an already-loaded backend without
prewarming, renaming, daemonizing, or shutting down a caller-owned pool. Newly
busy workers and raw lookalikes retain ordinary leak diagnostics.

Trio's native thread cache uses daemon threads and follows the existing daemon
exemption. Offload cancellation is not thread termination: abandoned Trio jobs
may outlive the runtime, while asyncio executor shutdown may wait. Neither engine
comprehensively attributes jobs on pre-existing threads. Application hooks remain
chained; threads cannot be forcibly stopped. Keep an external command deadline.

## Validation boundaries

`tests/test_runtime.py`, `tests/meta/test_trio_runtime.py`, and
`tests/meta/test_trio_adapters.py` exercise real native engines, explicit selection,
local/worker fixture lifetimes and TCP services, context isolation, shared setup,
cooperative deadlines, resistant work, Hypothesis handoffs, real ty, memory and
benchmark assertions, and OpenAPI/workflow/GraphQL services. Existing asyncio
regressions remain part of the same canonical suite. Installed wheel/source
archives must independently exercise both core-only rejection and optional engine
selection. Linux results do not establish native macOS/Windows signal, process,
or pipe-fault behavior.
