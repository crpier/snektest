# Changelog

Notable user-facing changes are recorded here.

## Unreleased

- Added deterministic, import-safe collection and strict empty-selection checks.
- Added bounded async cleanup, async-test timeouts, and task ownership diagnostics.
- Added process workers, run fixtures, memory assertions, and benchmark baselines.
- Added a deliberate public API and background-thread failure diagnostics.
- Added one cross-platform release gate and trusted, attested PyPI publishing.
- Rejected non-finite timeouts and documented the verified runtime and hard-timeout boundaries.
- Added reasoned SKIP, XFAIL, and strict XPASS outcomes across console, JSON, and programmatic runs.
- Capped each parameterized test at 10,000 Cartesian cases, removed the serial callback queue, bounded worker run-ahead, released unused passing output and traceback locals, and added large-suite memory budgets. (#38)

## 0.16.0

Earlier development release.
