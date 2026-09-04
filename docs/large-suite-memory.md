# Large-suite memory policy

Snektest collects a complete deterministic plan before execution. Memory therefore
grows with selected case count, but collection does not copy that plan into an
async queue or schedule one cross-thread callback per case.

## Parameter cardinality

Multiple `@test` parameter axes form a Cartesian product. A test with axis sizes
`a`, `b`, and `c` has `a * b * c` cases. Snektest accepts at most 10,000 cases
from one decorated test and raises `BadRequestError` before expansion when the
product is larger. Split larger matrices into focused tests or use
`@test_hypothesis` when examples should be sampled rather than enumerated.

The limit applies per decorated test. A suite may collect more than 10,000 cases
across separate tests and files.

## Result and output retention

The serial runner consumes the completed plan directly. It retains one compact
`TestResult` per executed case because programmatic summaries, benchmark-baseline
updates, final counts, warnings, and failure reports use those results.

Console and JSON runs discard captured output from a clean passing test after its
reporter receives that result. Failures and fixture-teardown failures keep their
output. `run_tests_programmatic` keeps passing output by default because the
returned `TestRunSummary` is the configured report. A custom reporter may set
`retain_passed_output = False` after consuming output in `test_finished`.

Normal runs snapshot exceptions into bounded immutable diagnostics, clear locals
from ended traceback frames, and release the live traceback. `--pdb` is the
exception: it keeps live frames until the first failure opens post-mortem and
stops the run. Large failing suites therefore retain one bounded diagnostic per
failure, not every failed test's object graph.

## Process-worker findings for #11

Current process workers still report in canonical manifest order. The
coordinator allows at most twice the worker count in active or
completed-but-unreported cases. If an
early case is slow, later workers stop receiving work when that window fills.
This prevents output and result objects from accumulating behind one blocked
ordinal. It trades some throughput for a predictable memory ceiling.

These are constraints for the process-parallelism architecture implemented in
#11, not a redesign of it: collection must stay complete and canonical,
dispatch must remain bounded, and reporters must declare when passing output is
worth retaining. More workers alone do not solve memory growth; without the
window, an early slow ordinal lets completed result objects accumulate.

## Measurements

Run the reproducible probe from the repository root:

```sh
uv run python benchmarks/large_suite_memory.py
```

A CPython 3.14.2 Linux run on 2026-09-04 produced:

| Scenario | Cases | Peak traced allocation | Retained traced allocation |
| --- | ---: | ---: | ---: |
| 10 x 100 collection | 1,000 | 0.64 MiB | 0.47 MiB |
| 100 x 100 collection | 10,000 | 6.22 MiB | 4.42 MiB |
| 64 KiB noisy passes | 1,000 | 1.70 MiB | 1.49 MiB |
| failures with 64 KiB frame locals | 1,000 | 4.37 MiB | 4.30 MiB |
| two workers with a slow first case | 1,000 | 3.55 MiB | 3.28 MiB |

The numbers are reference measurements, not portable timing gates. Automated
checks use explicit, looser allocation budgets: 4 MiB for 1,000-case collection,
24 MiB for 10,000-case collection, 8 MiB for noisy passes, and 16 MiB for the
large failing suite. The worker regression separately proves that two workers
complete no more than three later cases before a blocked first case finishes.
