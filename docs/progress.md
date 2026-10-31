# Lifecycle progress sidecars (#101)

`--progress-output PATH` creates a **new** UTF-8 JSONL sidecar before collection.
It never writes progress to stdout or captured user output. Use a fresh path for
each command; an existing file, symlink, missing parent, or existing companion
`PATH.lock` is an error before collection. There is no persistent configuration
key. Informational commands and invalid CLI arguments do not create a stream.

```sh
snektest --json-output --progress-output progress-001.jsonl tests/ >results.json
# In another terminal while the run is active:
tail -f progress-001.jsonl
```

Use `--progress-interval SECONDS` with `--progress-output` to set the periodic
snapshot interval (default 5 seconds, minimum 0.05, finite). Works with console
output, `-s`, collect-only, explicit workers, selection filters, fail-fast, and
JUnit. The progress path must differ from JUnit and baseline-update output paths.
Programmatic runners remain silent unless invoked through a CLI observation
scope; the existing `RunReporter` completion interface is unchanged. No new
observer types are exported from the top-level package in this slice.

## Version 1 records

Ordinary records carry `schema_version: 1`, a command-wide random `run_id`,
`event`, a **per-process** increasing `sequence`, monotonic `elapsed` seconds
since observation began, `pid`, and `worker`. The coordinator uses `coordinator`;
children use `snektest-fixture-host` or `snektest-worker-N`. Replacement workers
receive a new N and PID; consumers must not treat an execution slot as a stable
process incarnation. Use `(run_id, worker, pid, sequence)` as record identity.
The file's append order is observation order, not canonical result order or a
cross-process causal clock. Each process's elapsed values are nondecreasing.

Events:

- `run_started`: observation has opened, before project configuration/collection.
- `process_started` / `process_finished`: one process's observation lifetime.
  The latter includes `active_spans`; this means the observer retired, **not**
  proof of physical OS process exit or successful fixture cleanup.
- `collection_started` / `collection_finished`: one collection pass. Host and
  worker recollection each have their own records.
- `import_started` / `import_finished`: the current selected discovery file,
  including collection/schema expansion. Nested package/dependency imports are
  not separately hooked. An import cache hit may still have a file activity.
- `test_started` / `test_finished`: one actual execution occurrence, covering
  its body, function teardown, and ordinary result classification.
- `body_started` / `body_finished`: invocation/await of the body. Fixture loads
  inside that invocation are nested activities, not separate test occurrences.
- `fixture_setup_started` / `fixture_setup_finished` and
  `fixture_teardown_started` / `fixture_teardown_finished`: managed generator
  advancement where instrumented, with fixture name and `function`, `session`,
  or `run` scope. Cached loads do not repeat setup. Setup includes its source
  file when known. Teardown task-reaping and descriptor transport are not each
  separately instrumented. Standalone fixture-handle context managers are not
  registry-managed phases.
- `still_running`: bounded snapshots of current activities in that process.
  An empty list means no instrumented phase, not proof of a deadlock or idleness.
- `run_finished`: the final record on ordinary CLI completion, with `exit_code`,
  `status`, and `truncated`.

Activity start/finish pairs share a random `span` and `phase`. Both contain
`selector`, zero-based `ordinal`, and `attempt` (currently 1) for test-attributed
work; otherwise those fields are null. The selector uses the canonical collected
rerun spelling. Repeated selectors retain different ordinals. Nested programmatic
runners/direct executions inside a test do not introduce extra command test
occurrences or reuse its ordinals; their work remains inside the outer body.
Setup in the run
host and session/run teardown can have null test identity; session setup is
attributed to its initiating test, not every consumer. Async fixture teardown
uses the current teardown caller's identity rather than the fixture task's stale
initial context. In explicit workers, hosted run setup is observed in the host,
not misrepresented as setup inside a worker. `fixture`, `scope`, and `file` are
null outside applicable activities.

A finished test's `status` is its normalized test outcome (`passed`, `failed`,
`error`, `skipped`, `expected_failure`, or `unexpected_pass`); other activities
use `completed`, `error`, or `interrupted`. A failed body may therefore have an
`error` activity and an expected-failure test outcome. Activity status is not an
alternative result classification. The final run status is `completed` for exit
0, `failed` for an ordinary exit 1, `error` for command errors, or `interrupted`
for observed cancellation/KeyboardInterrupt/SystemExit. Interrupted SystemExit
retains its exit code. No exception messages are included in progress records.
Use the final result document for failure diagnostics and counts.

## Reading a live or incomplete stream

Read complete newline-terminated records; a concurrent write or process death can
leave a partial trailing record. Snapshot activities include `started_elapsed`,
so their observed age is the snapshot's `elapsed` minus that value. Follow the
innermost relevant fixture/import activity as well as its enclosing body/test.
Previously flushed records remain usable before a final result exists.

A missing `run_finished` means **incomplete command observation**, never success.
Even with a terminal record, unmatched activity/process starts can mean an
abruptly exited worker, a stopped observer, or interrupted work. Worker crashes
can produce a failed run and replacement records while the dead worker retains
unmatched spans. A graceful coordinator interruption can end the stream after
terminating children without their teardown events. Conversely, local cooperative
cancellation attempts bounded async fixture/task cleanup before termination.
Do not infer successful cleanup from a terminal event or interruption JSON.

The format is advisory observability, not a durable transaction log: flush is
not fsync. A partial/damaged record after hard process death is not a valid
completion marker. SIGKILL, power loss, crashes, repeated/hard interruption, or
stalled filesystem IO can prevent final records, JSON, or cleanup. Snektest does
not own or contain arbitrary subprocess trees. Signal inheritance remains as
specified in [CLI startup policy](cli-startup.md#posix-signal-policy-108).

## Resource and privacy limits

Each process retains at most 128 active metadata records. A heartbeat includes
at most 32, with `active_omitted` counting the rest; it does not duplicate the
collected plan or retain frames. Selector/file/fixture text is capped at 1,024
characters. `selector_truncated` and `metadata_truncated` explicitly identify
clipped text. Use the ordinal and final results to recover an oversized selector;
never guess a rerun spelling from a clipped string.

Output is capped at 16 MiB of ordinary events. A `stream_truncated` marker stops
further ordinary appends, reserving bounded space for `run_finished` with
`truncated: true`. This explicitly means an incomplete event history even if the
run itself completed. The marker carries schema/run identity and the byte limit.
Observation does not change which tests run when the cap is reached.

Writers serialize and flush appends using a short synchronous process-local lock
and native cross-process file lock; locks never span awaits. The companion lock
file keeps Windows readers separate from mandatory byte-range locking on the
event file. It is removed after normal coordinator retirement; hard termination
may leave it, without implying another live owner. Use a local filesystem with
working OS file locks. The observer is not a high-throughput benchmark tool or
a hard IO-time guarantee. File creation errors fail the command; later write
errors disable the affected writer and report a generic incomplete-stream warning
at retirement, without replacing test outcomes or corrupting JSON stdout.

A separate observer thread per participating process generates heartbeats, so a
cooperative synchronous wait can still be observed. A heartbeat scheduled on the
blocked event loop **cannot** run during that loop's synchronous block. This
thread is also not guaranteed CPU/GIL access: native calls holding the GIL,
scheduling delays, or blocked file IO can stop it. No heartbeat enforces a hard
sync/import deadline or proves deadlock. Keep an outer process/CI timeout.

No locals, argument values, captured user output, exception messages, thread
stacks, or coroutine frames are inspected or published. Explicit names and paths
are still user-controlled metadata and may themselves contain secrets; choose
safe parameter IDs and protect the sidecar like a test report. No automatic
redaction or automatic stack-dump facility is claimed.

## Validation

`tests/meta/test_progress.py` controls real subprocesses with file gates and
signals, observing records before releasing import/setup/body/teardown waits.
It covers repeated selectors, fixture scopes, success/failure/error, graceful
interruption, hard termination, capture opt-outs, worker crash/replacement,
collect-only, malformed options, existing evidence, and output truncation.
`tests/meta/test_distribution_artifacts.py` exercises independent wheel/sdist
installations through actual executables and isolated module entry points in
local/worker modes. Native Windows lock/signal behavior requires Windows
validation; Linux results alone do not establish it.
