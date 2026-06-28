# AGENTS.md

## Project Overview

snektest is a Python testing framework with first class support for async and static typing.

## Development Commands

### Guidelines for writing tests
- Do not use monkeypatching or mocking.

### Running Tests
```bash
# Run all tests
uv run snektest

# Run a specific test file
uv run snektest tests/test_myfeature.py

# Run a specific test function
uv run snektest tests/test_myfeature.py::test_something

# Run a specific parameterized case (case name in brackets)
uv run snektest tests/test_myfeature.py::test_something[case-name]

# Run one marker group
uv run snektest --mark fast
```

Explicit test-name and parameter-case filters are expected to error when the
requested test or case does not exist.

### Type Checking & Linting
```bash
# Type check
uv run pyright

# Lint/format
uv run ruff check
uv run ruff format --check
```

## Fixing linting issues
If possible let `ruff` fix issues automatically.

```bash
# Apply all automated fixes and format
uv run ruff check --fix .
uv run ruff format .
```

### Package Management
This project uses `uv` for dependency management. The project requires Python >=3.14.

## Architecture

### Test Collection & Execution Flow

1. **CLI Entry** (`cli.py:main`): Parse args, create filter items, start async event loop
2. **Producer-Consumer Pattern**:
   - Producer thread (`load_tests_from_filters`) walks filesystem, imports test modules, adds tests to async queue
   - Consumer coroutine (`run_tests`) awaits tests from the queue one at a time, on a single event loop, while collection continues in the producer thread
3. **Test Discovery** (`load_tests_from_file`): Import modules, find functions decorated with `@test()`, expand parameterized tests
4. **Test Execution** (`execute_test`): Capture stdout/stderr, execute test function (sync or async), teardown function fixtures, return `TestResult`

### Fixture System

Fixtures are generator functions decorated with `@fixture`, annotated
`Generator[T]` or `AsyncGenerator[T]`. Calling a decorated fixture returns a
handle (`Fixture[T]` / `AsyncFixture[T]`, defined in `annotations.py`); pass it to
`load_fixture()`. The handle carries the fixture's `scope`, a `key` (the
decorated function), and a `make` callable, so scope is read directly off the
decorator — no frame/annotation inspection. The handle is also a (async) context
manager, so fixtures double as setup helpers in standalone scripts.

All fixture state and teardown is owned by a `FixtureRegistry` (`fixtures.py`),
created fresh per run and reached ambiently through a `ContextVar` (set by
`run_tests` via `use_registry`). `load_fixture` is a free function that reads the
current registry — tests take no context parameter.

- **Function fixtures** (`@fixture`): Set up on each `load_fixture()` call,
  pushed onto the registry's function stack, torn down after each test in reverse
  (first-in-last-out) order. May take arguments, passed at the call site.
- **Session fixtures** (`@fixture(scope="session")`): Cached in the registry
  keyed by the decorated function, created on first `load_fixture()` call, reused
  across tests, torn down after all tests complete. Concurrent first-awaits of an
  async session fixture share one setup coroutine. Session fixtures must not
  accept parameters (enforced statically via the `@fixture(scope="session")`
  overload and at load time by the registry); use function fixtures for
  parameter-dependent setup, or return a factory/cache from a zero-argument
  session fixture.

```python
from collections.abc import AsyncGenerator

from snektest import fixture

@fixture
async def my_fixture() -> AsyncGenerator[str]:
    # setup
    yield "value"
    # teardown
```

### Markers

`@test(mark=...)` attaches a built-in marker describing the resources a test may
use: `"fast"` (in-memory, no IO/threads/subprocesses), `"medium"` (local IO or
threads), or `"slow"` (network IO, subprocesses, or other expensive external
resources). Marking every test is the recommended public style; filter a run to
one group with `--mark fast|medium|slow`. `Marker` (`decorators.py`) is the type
alias for the three literals; markers are passed as a single literal.

### Timeouts

`--timeout SECONDS` sets a run-wide ceiling on each test, applied in
`execute_test` by wrapping the awaited test body in `asyncio.timeout`. It is
async-only and best-effort: the timeout only fires while the test is suspended
on an `await`, so a hung `await` becomes an error (`TestTimeoutError`, reported as
ERROR) and the run continues, while synchronous or CPU-bound work cannot be
interrupted. A `TimeoutError` the test raised itself is distinguished from a
fired timeout via `Timeout.expired()` and passes through unchanged. Timed-out
tests still run function-fixture teardown. There is no per-test timeout; for
`@test_hypothesis`, prefer Hypothesis's own `deadline`/`max_examples`.

### Parameterization

Tests can accept multiple parameter sets via `@test([...], [...], mark=...)`,
each list built from `Param(value=..., name=...)`. `Param.to_dict()` creates all
combinations using `itertools.product`, keyed by param names. Each combination
becomes a separate test execution.

### Summary Output

Console summary lines intentionally keep exception details compact: only the first
exception message line is shown and long lines may be ellipsized. Use the full
failure details or `--json-output` for exact diagnostics.

### Assertions

Custom assertion system with rich error reporting. Use `assert_eq()` from
`snektest.assertions` rather than bare `assert`. Assertion helper argument order
is intentional: pass the observed/computed value first and the expected/reference
value second, following parameter names like `actual`, `expected`, `member`, and
`container`. Raises `AssertionFailure` with actual/expected values for better
error messages.

### Type Checking Configuration

Extremely strict pyright configuration (all checks set to "error"). When adding new code, expect to fully type-annotate everything. See pyproject.toml:69-174 for complete settings.

Notable exceptions to pyright rules:
- `reportIncompatibleVariableOverride = false`: Allow subclasses to override with different types
- `reportMissingSuperCall = false`: Don't require calling parent methods
- `reportImplicitOverride = false`: Don't require explicit `@override` decorator

## Documentation Surfaces

User-facing guidance lives in four places that must stay in sync. When changing
public behavior or recommendations, update all of them in the same change:

1. `README.md` — user docs
2. `snektest/agent_docs.py` (`AGENT_DOCS`) — embedded guide printed by `--agent-docs`
3. `snektest/examples/*.py` — bundled examples printed by `--example <name>`
4. This file (`AGENTS.md`) — contributor/architecture docs

Rules of thumb:
- The canonical import style in all examples is top-level: `from snektest import assert_eq, test`.
- Never hand-write sample test output in `README.md`; run the example with `uv run snektest` and paste the actual output.
- Code blocks in docs must type-check under this repo's pyright config and run as written.
- These rules are enforced by `tests/meta/test_doc_blocks.py`, which extracts every ```python block from `README.md` and `AGENT_DOCS`, type-checks them with pyright, runs them with snektest, and diffs each adjacent ```text block against captured output. Annotate exceptions with an HTML comment directive before the fence, e.g. `<!-- snektest-doc: expect-fail -->` or `<!-- snektest-doc: expect-type-error, skip-run -->` (see `testutils/docblocks.py`).

### Code Style Notes

- Ruff with extensive rules enabled (see pyproject.toml:22-64)
- Tests allow magic numbers, assert statements, private access (see per-file-ignores)
- Line length 88, but E501 ignored (long strings for messages okay)
- Mixed case names allowed in `annotations.py` for validators like `validate_SomeType`
