# Explicit stdlib log capture (#95)

`capture_logs(logger: str | logging.Logger, *, level: int = logging.DEBUG)` returns
one single-use `LogCapture` context manager. It is independent of the runner:
ordinary `with` works in synchronous functions, asynchronous functions, standalone
scripts, and fixtures. An explicit empty logger name selects root; `None` does not
silently select root. Invalid logger arguments, non-integer/boolean/negative levels,
and reentry/reuse raise `BadRequestError`.

## Records and rendering

`records` returns a tuple snapshot of original `logging.LogRecord` references in
handler delivery order. Tuple membership is stable, including after exit; record
fields and their values are **not frozen or copied**. Structured `extra` values,
raw `msg` and `args`, exception info, and thread metadata remain available.

`messages` takes a record snapshot and calls `getMessage()` outside handler and
registry locks. It does not apply application formatters (timestamps, exception
tracebacks, and custom prefixes are not appended). Each access renders anew;
mutable message arguments can change later output. Rendering exceptions produce
`<log message unavailable>` without invoking their message formatters or retaining
error text. `BaseException` interruptions propagate normally. Arbitrary user
message rendering can still block; this is not a hard timeout or sandbox.

Both snapshots are available before entry (empty), during capture, and after exit.
Each property read is independent; concurrent emissions can make two separate
reads cover different record sets.
They are not automatically serialized into console/JSON/JUnit results. The capture
object and caller-held snapshots retain potentially sensitive records until the
caller releases them; there is no redaction guarantee. Existing application
handlers may still print sensitive logs into ordinary output capture.

## Levels and logging semantics

The private handler uses the requested level. While active, capture lowers the
selected logger threshold only when needed; it never raises an existing lower
threshold and suppresses no application handlers. Lower thresholds are process-global
and can enable additional output from those handlers. A custom non-negative
integer level is valid. Unchanged levels are never written through `setLevel()`.
If an application-defined setter rejects restoration, the failure propagates,
but capture-owned registration is retired and other active scopes are refreshed;
the caller must repair any logger configuration that the setter refused. `NOTSET` means capture currently enabled emissions,
without lowering the logger threshold.

Standard logging routing still applies: child logger levels, source logger
filters/disabled flags, propagation barriers, and global `logging.disable()` are
not overridden. Ancestor logger filters/disabled flags retain stdlib semantics;
propagating child records are processed by ancestor **handlers**, not by the
ancestor logger's own filters. Existing handlers may modify original records
before or after capture sees them. Adding any handler also affects stdlib
`lastResort` routing: it is not used when the hierarchy contains a capture handler.

Nested and overlapping captures have independent handler thresholds, including
same-logger contexts exiting out of order. A process-local registry retains each
logger's original explicit level until its last capture exits. Active thresholds
are recomputed parent-first after entry/exit so an inheriting child's active
capture survives an earlier parent capture's exit. Only capture-owned handlers
are removed; application handlers, filters, propagation, disabled flags, and
handlers added by application code remain untouched.

Coordinate external logging reconfiguration separately. Concurrent changes to
levels, parent relationships, or wholesale logging reconfiguration while capture
is active are not merged with capture-owned level restoration. The API does not
install record factories or bypass standard logger filtering.

## Threads, async tasks, and fixtures

Capture includes matching emissions from **all threads** of its process and is
not contextvar/task-local. Overlapping async tasks can see each other's matching
records. Worker processes own independent registries: parent contexts do not
capture child-process records. No inter-process log transport is introduced.

The registry lock is held only during synchronous configuration changes; it is
never held through a context's body or an await. Handler locking serializes record
appends, snapshots, and retirement. Retirement stops acceptance before removal,
so a thread that selected a handler before exit cannot append to the ended capture
afterward. Delivery order is handler acquisition order, not a guaranteed global
wall-clock ordering across threads. Reentering one context is rejected before
altering an already-active capture; create a fresh context for each scope, including after failed entry. If temporary
configuration fails during entry, capture-owned state is rolled back.

A context in the test body exits before function fixture teardown. To include
teardown, put `with capture_logs(...)` **inside the generator fixture around its
yield**, and use `try/finally` for teardown logging and assertions. The bundled
`--example logging` demonstrates both structured records and async fixture capture.
Normal context-manager unwinding restores capture-owned configuration after body
assertion failures, exceptions, cancellation, and interruptions.

## Verification seams

- Public `capture_logs` with real stdlib loggers: hierarchy/routing, thresholds,
  structured record identity, rendering errors/interruptions, state restoration,
  nesting/overlap, threads, stable snapshots, and in-flight delivery after exit.
- Real CLI runs, local and persistent workers: sync/async fixture teardown,
  `to_thread` logging, no automatic result attachments, and timeout cleanup before
  the next case.
- Installed wheel/static exports and independent wheel/sdist smoke runs in normal
  and forced-lazy modes: typed snapshots and public exports survive distribution.

Downstream acceptance uses a copied fifth-snekql lifecycle logging test with its
custom recording handler/context removed. Application assertions filter the public
records/messages; no additional framework search/assertion methods are required.

## Downstream validation

A copied `lifecycle_verbs_emit_events` from fifth-snekql's
`tests/sqlite/test_structured_logging.py` at
`2238a462ed3c26fa122e393de126e840e21fc3f4` passed in normal and forced-lazy
modes. Its recording handler and capture context were removed; the replacement
uses `capture_logs("snekql")` and filters zipped record/message snapshots after
exit for the original level-specific lifecycle assertions. The copied test also
passed strict ty. Downstream tracked files and its existing environment were
unchanged; execution used the previously isolated environment and required
`PYTHON_CONTEXT_AWARE_WARNINGS=1` setting.

The bundled `--example logging` source passed as a discoverable `test_logging.py`
file. Initial complete validation passed 970 cases in each import mode, with 94%
core coverage and 100% coverage of `log_capture.py`. Integrating concurrent #105
preserved its installed-package tests alongside the new capture tests; the fresh
combined release-gate results are recorded on the pull request.

The machine's `/tmp` user quota exhausted during an earlier gate attempt. A
successful rerun used a task-owned `TMPDIR` under the home filesystem, retaining
the same tests, coverage threshold, memory budgets, and artifact checks. CI uses
its normal runner temporary directory.
