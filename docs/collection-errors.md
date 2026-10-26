# Collection error diagnostics

Console collection failures lead with the selected file/filter, original exception
type and message, then relevant user-code locations and source. Framework,
interpreter, and installed-dependency plumbing is omitted from the default frame
list. The latest error appears before its causes so long chains cannot consume
the display budget before the actionable error is shown.

Use the same command with `--full-traceback` to include framework/interpreter and
dependency frames. This is a console presentation option, not a different
collection mode or a project-configuration key:

```sh
snektest tests/test_example.py
snektest --full-traceback tests/test_example.py
snektest --full-traceback --collect-only tests/test_example.py
snektest --full-traceback --workers 2 tests/test_example.py
```

## Context and limits

Both modes preserve explicit causes, unsuppressed implicit context, nested
exception-group members and exception notes. `raise ... from None` stays
suppressed. Syntax errors include their own filename/line, a source window, and
caret context, including when the compiler failed before the test module could
execute. A helper-import failure still identifies the selected test file in its
heading. User-raised `CollectionError` messages are retained rather than mistaken
for Snektest's outer collection wrapper.

Bad `Param` construction keeps its original `TypeError` and adds corrective
`Param(value=..., name=...)` / tuple-value guidance. Bare decorators retain the
existing `@test()` correction. No constructor or validation rules are relaxed.
Message formatters that raise use a safe `<str failed: ErrorType>` fallback
without replacing the original exception type or collection exit status.

Full detail means **including internal frames**, not unlimited output or local
variable dumps. Console reports have roughly 16,000 characters of content by
default or 60,000 with full detail, plus a short footer. Individual fields are
limited to 2,000 / 4,000 characters. Traversal permits at most 50 unique exception
nodes, depth 10, 10 direct group members, 10 notes per exception, and 200 traceback
frames per exception. The default shows at most the six most recent user frames.
Syntax source uses a 400-character window around the error. Cycles, repeated
nodes, omitted members/frames, and text truncation are explicitly marked with a
truncation notice. These are display limits, not promises to interrupt a hung
user-supplied formatter or source-file read.

Paths/source/messages may contain application information. No automatic
redaction is promised. Redirected console lines retain paths without artificial
Rich wrapping so they remain copyable.

## Structured output and workers

`--full-traceback` has no effect on JSON documents. JSON schema version 1 retains
its existing exception snapshot, collection output/warnings and uncaptured
output. Use `--json-output` when captured collection output or structured causes
are needed; a console rerun with full detail is the route to framework frames.
Collection failures still exit 2, complete imports before any body executes, and
preserve all selection/empty-plan/decorator checks.

The fixture host formats both bounded console variants before sending a
collection failure to the coordinator. These strings are private transport data,
not new JSON fields. They preserve syntax details and child-process framework
frames that cannot be reconstructed from a parent traceback. Normalized exception
snapshots remain independent and unchanged.

When changing reporting, test through the real console/JSON subprocess seams in
`tests/meta/test_collection_diagnostics.py`, including local and worker mode,
without asserting private formatter methods or unstable framework line numbers.
