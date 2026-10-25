# Equality diagnostics (#94)

`assert_eq(actual, expected)` still evaluates the original `actual != expected`
condition exactly once. Comparison exceptions and interruptions propagate as
before; no SQL normalization, coercion, or alternate definition of equality is
introduced. Passing comparisons do not load `equality.py`.

## Explanations

- Single-line strings longer than 160 characters: first differing **zero-based
  Python character index** (not byte offset) and up to 40 characters of context
  on each side. The end of either string also counts as a difference.
- Multiline strings: unified diff from `expected` (`-`) to `actual` (`+`). A
  newline-only difference falls back to character context rather than vanishing
  during line splitting.
- Exact built-in dictionaries: missing keys (expected only), extra keys (actual
  only), and differing keys, retaining insertion order rather than sorting mixed
  key types. Nested paths identify values such as `$['types'][1]['name']`.
- Exact built-in lists and tuples: first differing index, nested details, and
  length mismatches. Tuple/list type distinctions remain significant.

A non-empty `msg=` replaces the automatic explanation exactly, including embedded
newlines. An empty message preserves the existing default-message behavior.
`AssertionFailure.actual`, `.expected`, and `.operator` remain unchanged for direct
callers. Console details, normalized exceptions, workers, JSON, and JUnit carry
the same message; no JSON fields or schema version changes are needed.

## Bounds and conservative inspection

Generated output is at most **8,000 characters**. Inspection is also bounded:

| Work | Limit |
| --- | ---: |
| Common string prefix scan | 100,000 characters |
| Multiline input | First 100,000 characters, 200 lines, 256 characters/line |
| Unified diff output | 80 lines (also subject to the total text cap) |
| Per-container comparison | 50 items |
| Comparison traversal | 1,000 nodes, depth 5 |
| Preview traversal | 100 nodes, depth 5, 6 items/container |
| Preview strings/bytes | First 160 units |
| Dictionary lookup keys | Exact primitive builtins; at most 256 string/byte units or 2,048 integer bits |

Limit notices explicitly say that comparison detail was truncated. They do not
turn a failure into a pass, claim a later index is the first mismatch when an
unexamined prefix remains, or attempt a complete scan of large mappings.

User `repr`, additional custom equality, iteration, indexing, and metaclass name
hooks are not used by diagnostics. Custom objects and builtin subclasses are
opaque, not structurally traversed. Unknown nested values stop localization
rather than falsely attributing a mismatch to values whose custom equality may
have succeeded. Unsupported/custom dictionary keys also stop localization:
looking up even a primitive key could otherwise invoke a colliding custom key's
comparison. Large integers are described by bit length without constructing a
potentially enormous decimal representation. Cycles in previews are marked.
Unexpected inspection errors (for example concurrent dictionary mutation) use a
fixed fallback, leaving the established assertion failure intact. Real
interruptions are not suppressed.

These bounds exclude the original equality operation, which belongs to the user's
objects, and explicitly provided custom messages. They are not hard CPU deadlines
for arbitrary user code.

## Reporting ownership

`equality.py` builds the complete safe message only after a mismatch. Assertion
snapshots recognize equality's `operator="=="` and retain that message without
re-inspecting raw operands. The presenter prints it without legacy repr/diff
processing. Other assertion helpers and manually constructed non-equality
failures keep their existing rendering behavior. This avoids duplicate work,
unsafe snapshot traversal, and differences between local and worker reporting.

Tests cross the public `assert_eq` interface and CLI/JSON worker interface. They
cover SQL strings, structured reports, expected/actual orientation, custom
messages, cycles, bounds, opaque/hostile values, original comparison exceptions,
and deferred formatter loading. README output is captured from a real run.

## Validation

The complete local Linux release gate passed **935 tests in normal mode** and
**935 in forced-lazy mode**, including inherited subprocess mode. Core coverage
was **94%** (the equality module itself: **92%**). Ruff formatting/lint, strict ty,
production dependency audit, independently built/installed wheel and source
archives, and documentation block checks passed. The bundled basic example also
passed when copied to a discoverable `test_basic.py` file.

Using the isolated downstream environment, fifth-snekql at
`0411c036b680c04a6c7f679db5985da21d0dfa7a` passed all **680** selected query,
identifier-quoting property, and schema-compilation property cases in each mode,
without modifying tracked downstream files or its existing environment. This is
not a full downstream acceptance run; the earlier unrelated limitations remain
recorded in `docs/python315-migration.md` and `docs/lazy-imports.md`.

Cross-platform verification of this change awaits its own CI matrix. The Linux
results above do not imply Windows or macOS validation of the new diagnostics.
