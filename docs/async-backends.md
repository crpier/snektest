# AnyIO integration and async backend policy (#100, first slice)

## Implemented: AnyIO operations on asyncio

Snektest has one async runtime: asyncio. The optional `snektest[anyio]` extra
selects AnyIO `>=4.14.2,<5`; core installation/execution does not require AnyIO.
Await AnyIO operations directly from async tests and fixture setup/teardown.
Do not call `anyio.run()` or start a nested event loop inside them. Print
`snektest --example anyio` for a runnable example of thread offloads, task groups,
and separate fixture operations. Local, spawned-worker, and programmatic runs
keep their existing lifecycle, results, JSON schema, and best-effort timeouts.
An embedding caller may run the programmatic runner under
`anyio.run(..., backend="asyncio")`; that caller still owns its runtime/pool.

Task-local AnyIO cancel scopes and task groups must be entered and exited in
one task. A test body can use them normally. A fixture can finish a complete
scope inside setup or inside teardown, but **cannot keep it open across yield**:
Snektest may execute those phases in different tasks. This also applies to
library context managers that internally hold such a scope. Use independently
awaited operations or existing asyncio-owned fixture tasks instead; this slice
does not change fixture task identity to support long-lived AnyIO nurseries.

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
not comprehensively attributed. This slice does not claim to detect every
abandoned offload. Threads cannot be cancelled or forcibly reaped. Cancellation
and timeouts may stop awaiting while work continues and delays shutdown; use an
outer process/CI deadline. See [the hard-timeout boundary](adr/0001-hard-timeout-boundary.md).

## Not implemented: choosing another runtime

There is no backend CLI flag, configuration key, decorator setting, or
programmatic selector in this slice. In particular, AnyIO API compatibility
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
  thread handoffs, and shutdown through a coherent runtime boundary. Decide how
  task-local fixture scopes survive their yield before promising AnyIO nurseries.
- Keep normalized results, process-neutral failures, selection/order, and
  structured reporting independent of the chosen backend.
- Require the selected optional dependency and reject an unavailable/unsupported
  backend clearly, without silent fallback. Default asyncio must remain usable
  without Trio or AnyIO installed.
- Respect an embedding application's running loop; a programmatic runner cannot
  replace its caller's runtime or nest an incompatible event loop.
- Preserve the external hard-timeout requirement; another backend is not
  process-per-case isolation.

Backend selection and Trio remain follow-up work on #100; this compatibility fix
must not be treated as completion of that broader request.
