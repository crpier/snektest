# AnyIO integration, async fixture lifetimes, and backend policy (#100)

## Implemented: AnyIO operations on asyncio

Snektest has one async runtime: asyncio. The optional `snektest[anyio]` extra
selects AnyIO `>=4.14.2,<5`; core installation/execution does not require AnyIO.
Await AnyIO operations directly from async tests and fixture setup/teardown.
Do not call `anyio.run()` or start a nested event loop inside them. Print
`snektest --example anyio` for a runnable example of thread offloads, task groups,
and a fixture-backed async service. Local, spawned-worker, and programmatic runs
keep their existing results, JSON schema, and best-effort timeouts.
An embedding caller may run the programmatic runner under
`anyio.run(..., backend="asyncio")`; that caller still owns its runtime/pool.

## Task-affine async fixtures

Each managed async generator fixture has one persistent owning task. Setup,
its suspended yield, and teardown all run in that task, in function, session,
and run scopes. AnyIO cancel scopes, task groups, and library context managers
holding them may span an async fixture's yield. Each dependency has its own
task/scope stack, so a function fixture can open a scope before lazily loading a
longer-lived dependency without violating cancellation-scope LIFO ordering.
The process fixture host keeps its loop running while awaiting commands, so
async services remain responsive between setup and shutdown requests.

The task inherits the loader's initial context. ContextVar updates remain
fixture-local; setting one does not implicitly bind the test task. Pass
resources/context explicitly as yielded values. A scope surrounding the yield
governs the fixture and its descendants, **not the test body**. A background
task-group error is attributed to that fixture at its scope teardown; it does
not automatically cancel the test. Protect service setup/yield with try/finally
and cancel or finish long-running children during teardown. Normal task-group
exit waits for children; it is not a request to stop a server.

Synchronous generator fixtures still load synchronously. Task-local runtime
scopes spanning yield require async fixtures. Standalone handle context-manager
use continues to run in the caller's task; this owning-task policy applies to
registry-managed fixtures only.

Shared cached setup survives cancellation of an individual waiter. Function
setup follows its waiter cancellation and still receives fixture cleanup.
Unfinished function loads are accounted for even before their owning task first
runs. Function loads after function teardown starts are rejected, including
from abandoned-task finalizers. Every attempted setup retains cleanup ownership;
dependency order, descriptor publication, outcome classification and configured
cleanup budgets remain in force. Cancellation is cooperative, not a hard limit.

Setup warnings are relayed when a value/error reaches its waiter. Subsequent
fixture-owned warnings are relayed during fixture teardown (including cancelled
descendants), rather than lost in an expired setup warning context. Function
warnings join that test's warnings; session/run teardown warnings use their
existing fields. In process mode, run-host setup warnings travel with existing
run-fixture setup output. Capture-disabled runs keep normal warning delivery.
When context-aware warnings are explicitly disabled, stdlib's process-global
capture stays phase-local; no lifetime-spanning catcher is installed.

## Thread observability

AnyIO's asyncio backend keeps non-daemon workers alive while idle. Returning
from a completed `await anyio.to_thread.run_sync(...)` is not a thread leak.
`thread_observation.py` recognizes only workers present in both the current
loop's registered-worker set and idle-worker deque, with matching loop identity.
It reads the already-loaded backend; it does not import AnyIO merely because
it is installed, submit warmup work, change daemon flags, or stop pools.

There is no public AnyIO thread-identity query. The private RunVar lookup is
isolated in one helper; unknown registry shapes fail closed (no exemption),
rather than falling back to thread names or allowing all backend threads.
Dependency updates must rerun public local/worker/installed-archive regressions.
A renamed idle worker is still recognized, a raw thread named `AnyIO worker
thread` still fails, and a new worker executing abandoned work is not exempted.
Unhandled-thread/unraisable hooks and exception/error classification are
unchanged. AnyIO task-group exception groups remain ordinary errors.

The observation window still measures **new threads**, not every outstanding
job on pre-existing threads. Busy work on a previously observed pool worker is
not comprehensively attributed. This integration does not claim to detect every
abandoned offload. Threads cannot be cancelled or forcibly reaped. Cancellation
and timeouts may stop awaiting while work continues and delays shutdown; use an
outer process/CI deadline. See [the hard-timeout boundary](adr/0001-hard-timeout-boundary.md).

## Not implemented: choosing another runtime

There is no backend CLI flag, configuration key, decorator setting, or
programmatic selector yet. In particular, AnyIO API compatibility
**does not imply Trio support**. The runner, fixture/task cleanup, Hypothesis
cross-thread handoffs, timeouts, and persistent child event loops use asyncio.
Passing an asyncio test through `anyio.run(..., backend="trio")` does not adapt
those lifetimes, and silently falling back to asyncio would conceal the choice.

Requirements for a future runtime-selection design, not current API promises:

- Preserve asyncio as the default; validate an explicit command-wide choice
  before imports/execution. Avoid implicit backend detection or per-case runtime
  switching within one fixture cache.
- Apply that choice to local execution, fixture host, original execution workers,
  and replacement workers. Recollection/replay must retain the same runtime.
- Own fixture setup/teardown, task discovery/ownership, cancellation, timeouts,
  thread handoffs, and shutdown through a coherent runtime boundary. Preserve
  task-affine async fixture lifetimes and explicit context isolation.
- Keep normalized results, process-neutral failures, selection/order, and
  structured reporting independent of the chosen backend.
- Require the selected optional dependency and reject an unavailable/unsupported
  backend clearly, without silent fallback. Default asyncio must remain usable
  without Trio or AnyIO installed.
- Respect an embedding application's running loop; a programmatic runner cannot
  replace its caller's runtime or nest an incompatible event loop.
- Preserve the external hard-timeout requirement; another backend is not
  process-per-case isolation.

Backend selection and Trio remain follow-up work on #100; these integration
increments must not be treated as completion of that broader request.
