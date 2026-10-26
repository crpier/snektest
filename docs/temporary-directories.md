# Runner-managed temporary directories

`temporary_directory(*, base=None, retention="never") -> pathlib.Path` allocates a
unique private directory during a Snektest test or fixture setup. It works without
`await` in both sync and async functions. The runner owns cleanup; the returned
value is an ordinary absolute `Path`, not a fixture handle or context manager.
Print `snektest --example temporary` for a complete executable example.

## Lifetime and outcome policy

- A call from a test or function-fixture setup belongs to that test. All ordinary
  function fixtures finish first, then test-owned task cleanup and background
  classification finish, then directory policy runs before the result is reported.
  A dependent service can therefore stop/reap its processes before root deletion.
- A call during session-fixture setup belongs to that execution-process session.
  It survives between cases and through all ordinary session teardown/task cleanup.
  A cached session fixture should allocate once and yield the path. Calls are not
  cached by themselves: every call allocates a fresh root.
- Scope follows the active setup context, not where a factory function was defined.
  `asyncio.to_thread` copies this context; raw threads do not. Allocate paths before
  offloading resource work when possible. Allocation from collection, completed
  lifetimes, fixture teardown, or run-fixture setup is unsupported and rejected.
- Run fixtures and standalone fixture context-manager use are not supported by
  this resource. Use stdlib `TemporaryDirectory` outside the runner; command-wide
  run-fixture ownership would need a separately designed distributed outcome policy.

| `retention` | Behavior |
| --- | --- |
| `"never"` (default) | Attempt cleanup even after a test/setup/resource-teardown failure. |
| `"on-failure"` | Keep evidence for actionable failures or interruption observed before directory policy. |
| `"always"` | Keep successful artifacts too. |

Function retain-on-failure includes FAIL, ERROR, XPASS, ordinary fixture teardown
failures, test/fixture task leaks, and observed background failures. Caught
exceptions (including `assert_raises`), successful tests of failing child programs,
SKIP and XFAIL do not trigger retention. Setup failure before the fixture's first
yield still leaves an owned directory eligible for cleanup or retention.

Session retain-on-failure is deliberately conservative and **process-local**: any
actionable test failure observed by that registry, even before the directory was
allocated or in an unrelated case, or any ordinary session-teardown failure keeps
its session roots. It is not dependent-test-only or whole-command policy. Other
workers' failures and later host/run-fixture teardown errors do not retroactively
change it. Replacement workers start fresh registries and unique roots.

Directories are finalized in reverse allocation order within their lifetime.
A removal error retains a path record, adds an ordinary fixture-teardown failure,
and causes later on-failure roots in that phase to be kept. Previously removed
roots cannot be restored. Do not interpret retention as rollback.

## Placement and ownership

`base` accepts a string or `os.PathLike[str]` naming an existing directory; `None`
uses Python's normal temporary-base selection. An explicitly supplied base avoids
small tmpfs quotas without needing a per-command environment setting. Allocation
uses short random `sqt-` leaves rather than test names, is unique across concurrent
commands/workers, and uses stdlib `mkdtemp` private permissions (0700 on POSIX).
Choose a short base yourself when resources use Unix-domain sockets.

Pass the path explicitly to libraries. For example, SnekQL's
`temporary_mariadb_server(runtime_base_directory=root)` can place its runtime/logs
and default data under that owner. For child processes, explicitly pass the root
as an argument or in that child's environment and wait for child completion.
Snektest does not change process-global `TMPDIR`, relocate library-created paths,
or sweep a reusable checkout database such as `.snektest/mariadb-data`.

Change the contents freely, but keep the root at its allocated pathname. Removal
checks the allocated root's device/inode and rejects replacements or symlinks;
already-absent roots are harmless during cleanup. Only allocated roots are passed
to `shutil.rmtree`, never their caller-owned base. Nested symlinks follow stdlib
`rmtree` safety behavior. This is not a sandbox against hostile concurrent changes
to caller-controlled ancestor directories; stop resource consumers before cleanup.

`"never"` still attempts deletion after a resource shutdown failure. Choose
`"on-failure"` when retaining such evidence is safer; the suite remains responsible
for stopping/reaping resource processes and deciding whether deletion is safe.

## Reporting, cancellation, and limitations

Completed console runs print retained **paths**, scope and reason without reading
or attaching contents. Programmatic `TestResult.retained_directories` records
function paths; `RunResult.retained_directories` aggregates function and session
paths. JSON schema version 1 adds optional `retained_directories` arrays at those
same levels. Each
record contains `path`, `scope`, `retention`, and `reason` (`always`, `failure`, or
`cleanup_error`). No field is added when nothing is retained. Cleanup errors use
existing fixture/session teardown counts and JUnit synthetic-error cases; the
original test failure remains primary. These are locations, not file uploads or
redaction guarantees. Artifacts can contain credentials.

Large recursive deletion runs in an executor thread, not on the shared event-loop
thread. Caller cancellation, including repeated requests, waits for the deletion
attempt before propagating. Resource teardown retains its existing bounded cleanup
machinery. Filesystem threads themselves cannot be cancelled: removal may exceed
`--timeout` and a stalled filesystem can delay runner/event-loop shutdown. An outer
process/CI deadline is still necessary. Cancellation arriving after deletion has
begun cannot bring the directory back.

Graceful body interruption still attempts resource and directory cleanup, but an
interrupted run may never produce a completed retained-path report. Abrupt worker
exit, infrastructure termination, SIGKILL, machine failure, or interruption during
allocation can orphan roots. No automatic discovery, cleanup of previous worker
incarnations, or historical pruning is offered. Explicitly retained/orphaned roots
can accumulate: inspect your known owned paths and remove them deliberately rather
than sweeping arbitrary `/tmp` entries.

When changing lifetime/outcome ordering, thread cleanup, ownership checks, or worker
transport, exercise real local/worker CLI and JSON runs in
`tests/meta/test_temporary_directory.py`, embedding cancellation/progress checks,
and the independently installed-wheel typing/runtime checks.
