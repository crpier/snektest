# Python 3.15 migration (#92)

## Scope

Snektest 0.17 requires Python >=3.15. The release matrix targets GIL-enabled
CPython 3.15 on Linux, macOS, and Windows; free-threaded builds and other Python
implementations remain unsupported. Newer CPython versions are installable but
unverified. Explicit lazy imports are separate work in #93.

The local interpreter is stable CPython 3.15.0, not a prerelease. Existing virtual
environments must be recreated after upgrading. No runtime branches specific to
Python 3.14 were found to remove. Historical benchmark measurements and synthetic
machine fingerprints mentioning 3.14 remain historical data, not support claims.

## Dependency and toolchain review (2026-10-25)

Only the dependencies needed for the migration were refreshed; the schema
integration retains its existing Schemathesis compatibility range.

| Component | Previous lock/pin | Selected lock/pin | Reason |
| --- | --- | --- | --- |
| uv | 0.9.26 | 0.13.4 | Current local toolchain; includes a certificate security update. Both workflows and the project require the same exact version. |
| Pydantic | 2.12.4 | 2.14.1 (minimum 2.14.0) | 2.14 explicitly adds Python 3.15 support; 2.14.1 includes validation fixes and its matching pydantic-core 2.50.1. |
| Coverage | 7.13.1 | 7.17.0 | Published CPython 3.15 wheels, monitoring fixes, and a fix for lost parallel coverage data on repeated saves. |
| Ruff | 0.14.5 | 0.17.2 | Python 3.15 target support; configure `target-version = "py315"`. New lint checks and Markdown code-block formatting require mechanical adjustments. |
| ty | 0.0.75 | 0.0.84 | Python 3.15 support, the security fix in 0.0.84, and alignment with snekql's pinned checker. Pin exactly to keep strict diagnostic expectations reproducible. |
| pip-api | 0.0.34 | 0.0.35 | 0.0.34 imports removed `sre_constants` and crashes the audit stage on 3.15. Add an explicit development minimum for the compatible release. |

Pydantic also refreshes typing-extensions (4.16.0) and typing-inspection (0.4.4).
Its improved TypeAdapter typing makes several test casts redundant. The newer
checker requires explicit generic fixture narrowing and removal of unreachable
conditions; no ty rules were disabled. The worker's runtime check for arbitrary
SystemExit codes is retained despite the narrower stdlib annotation.

Upstream release notes reviewed:

- [uv 0.13.4](https://github.com/astral-sh/uv/releases/tag/0.13.4)
- [Pydantic 2.14.0](https://github.com/pydantic/pydantic/releases/tag/v2.14.0)
  and [2.14.1](https://github.com/pydantic/pydantic/releases/tag/v2.14.1)
- [Coverage 7.17.0](https://github.com/nedbat/coveragepy/releases/tag/7.17.0)
- [Ruff 0.17.2](https://github.com/astral-sh/ruff/releases/tag/0.17.2)
- [ty 0.0.84](https://github.com/astral-sh/ty/releases/tag/0.0.84)
- [pip-api 0.0.35](https://github.com/di/pip-api/releases/tag/0.0.35)

## Validation

The compatibility regressions were first updated to the new contract and failed
against the old README/metadata/workflow settings, then passed after migration.
Artifact tests independently install and smoke-test wheel and source archives,
and ask uv to resolve each archive for a clean Python 3.14 target; both must be
rejected because of `Requires-Python`. The successful install explicitly selects
the running 3.15 interpreter instead of relying on a workstation default.

On Linux x86_64 with CPython 3.15.0 and uv 0.13.4, the complete command
`uv run --locked python scripts/release_check.py` exits 0: **896 tests pass**,
core-package coverage is **94%** (above the unchanged 90% floor), and the production
dependency audit reports no known vulnerabilities. This includes wheel/sdist
builds, installs, metadata rejection checks, and installed public API tests.
Cross-platform verification still requires the GitHub Actions matrix; local
Linux results do not establish a macOS or Windows pass.

### Downstream setup

The downstream checkout is fifth-snekql commit
`395f731c58916109a21b606777fb1729543e92a8` (snekql 0.9.0). Validation uses a separate
temporary Python 3.15 environment with its exported locked dependencies and an
editable install of this snektest checkout. The final run sets
`PYTHON_CONTEXT_AWARE_WARNINGS=1`, matching snekql CI, and preserves normal POSIX
SIGINT handling. Its existing environment and tracked
files are unchanged. This also exercises the Pydantic 2.14.0 minimum, rather than
only the 2.14.1 lock used by snektest's own release gate.

The unmodified `python -m snektest tests` command stops during collection:
`tests/runtime/test_collations.py::sqlite_rejects_unsupported_collations` derives
an empty parameter-case name from an empty string. Snektest 0.17 already rejects
empty case IDs; this is an existing 0.16-to-0.17 migration requirement, not a Python
3.15 regression. Do not relax collection validation as part of #92.

A separate broad validation excludes that file and passes `--allow-empty`
because individual positional files include static-only typing tests with no
decorated runtime cases. The exclusion is explicit; this is not a claim that the
unmodified downstream suite passes.

### Downstream results

- Query compilation suite (`tests/query`): **671 passed**.
- Query plus SQLite suites (`tests/query tests/sqlite`): **1017 passed, 1 failed**.
  The failure is the connectivity acquisition-deadline case reporting a leaked
  non-daemon `AnyIO worker thread`.
- Running `tests/sqlite/test_acquisition_deadlines.py` against a source snapshot
  of unchanged `origin/main` (`cdbe15d`), on the same interpreter and dependency
  environment, reproduces that exact failure (**3 passed, 1 failed**). Thus this
  thread-observation incompatibility predates the #92 changes.
- An OpenTelemetry-only selection against unchanged `origin/main` also reports
  a non-daemon `ThreadPoolExecutor` leak. Exact affected cases vary with thread
  shutdown timing; do not claim a clean downstream run from isolated passing
  cases.
- The broad run with the collection exclusion and CI warning configuration
  reached **3937 passed, 18 failed, 24 errors**, then was interrupted cleanly
  after **3979 of 4021** selected cases. In addition to thread leaks, MariaDB
  initialization reported filesystem/quota error 122 while preallocating data
  files. This is not a clean acceptance run, and its resource-dependent failures
  have not all been triaged. Orphan MariaDB processes attributable to the earlier
  validation attempt were stopped; the final runner performed fixture teardown
  before exit. A subsequent standalone password-bootstrap test initialized its
  server successfully but still failed thread-leak classification.

The unmodified downstream suite is therefore **not green**. Before declaring
snekql fully migrated to 0.17, fix its empty case IDs, investigate AnyIO and
OpenTelemetry thread lifetime/attribution, and rerun on a filesystem with enough
space/quota for temporary MariaDB instances. These are recorded rather than
weakening collection or thread hygiene in the Python-version migration.
