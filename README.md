# snektest

A type-safe, async-native Python testing framework.

## Installation

```bash
uv add snektest
```

OpenAPI contract testing is optional:

```bash
uv add 'snektest[schema]'
```

## Type checking is part of the contract

snektest expects your test code to pass the strict `ty` type checker before
tests run — typically continuously, via your editor's language
server. The API is designed around this: signatures are exact, and snektest
does not re-validate at runtime what a type checker already rejects. If you
skip type checking, misuse that a checker would flag — such as applying
`@test` without parentheses — can fail silently at runtime.

Runtime validation is reserved for what static checkers cannot see: CLI
input, file paths, and fixture protocol rules (for example, session fixtures
must not accept parameters).

<!-- snektest-doc: expect-type-error=no-matching-overload@5, skip-run -->
```python
from snektest import test


# @test must be *called*: applying it bare is a type error, not a runtime one.
@test
def test_needs_parentheses() -> None:
    pass
```

## Quick Start

Create a `test_*.py` file. The recommended style is to mark every test with the resources it may use:

```python
from collections.abc import AsyncGenerator

from snektest import assert_eq, fixture, load_fixture, test

@fixture
async def provide_number() -> AsyncGenerator[int]:
    yield 2

@test(mark="fast")
async def test_basic_math() -> None:
    given_number = await load_fixture(provide_number())

    result = given_number * 2
    assert_eq(result, 4)

@test(mark="fast")
def test_strings() -> None:
    assert_eq("hello".upper(), "HELLO")
```

Run your tests:

```bash
snektest
```

Run one marker group when you want focused feedback:

```bash
snektest --mark fast
```

## Features

### Fixtures

Define fixtures as generator functions decorated with `@fixture`, annotated
`Generator[T]` or `AsyncGenerator[T]`. `@fixture` (the default) is
function-scoped: set up and torn down for each test. `@fixture(scope="session")`
is set up once per execution process and reused there. `@fixture(scope="run")`
is a zero-argument, module-level fixture owned once by the command; it lazily
yields an inert stdlib-pickle descriptor of at most 1 MiB, and each worker gets
an independent decoded copy. Calling a decorated fixture returns a handle; pass
it to `load_fixture()`. Fixtures may take arguments, passed at the
call site (e.g. `load_fixture(make_user("Ada"))`), and calling one twice yields
two independent instances. Session and run fixtures must not accept parameters.
Use a function fixture for parameter-dependent setup, or have a zero-argument
cached fixture return a factory/cache.

Set up and tear down test dependencies with session-scoped fixtures:

```python
from collections.abc import AsyncGenerator

from snektest import assert_eq, fixture, load_fixture, test

@fixture(scope="session")
async def connection_pool() -> AsyncGenerator[dict[str, str]]:
    # Setup: runs once for all tests
    pool = {"host": "localhost", "status": "connected"}
    yield pool
    # Teardown: runs after all tests
    pool["status"] = "disconnected"

@test(mark="fast")
async def test_connection() -> None:
    pool = await load_fixture(connection_pool())

    assert_eq(pool["status"], "connected")
```

A fixture handle is also a context manager, so fixtures double as setup helpers
in standalone scripts (no runner needed): `with user_fixture() as user: ...` or
`async with connection_pool() as pool: ...`. In standalone use there is no
runner, so scope is ignored and each block does its own setup and teardown.

#### Fixtures depending on fixtures

A fixture can depend on another by calling `load_fixture()` in its own body. The
rules:

- A **function** fixture may depend on a function, session, or run fixture.
- A **session** fixture may depend on another session or run fixture.
- A **run** fixture may depend on another run fixture.
- A **session** fixture may **not** depend on a function fixture: the session
  fixture is cached for the whole run and would outlive the per-test
  dependency, so snektest raises `FixtureError`.
- A function fixture depending on a session fixture reuses the cached session
  instance, exactly like a test would.
- An **async** fixture may depend on a sync or async fixture (await the async
  ones). A **sync** fixture can only depend on sync fixtures, since its body
  cannot await an async dependency.
- **Teardown is depending-fixture-first**: a fixture is torn down before the
  fixtures it loaded, so it may safely use them during its own teardown. This
  holds across function, session, and run scope.

```python
from collections.abc import Generator

from snektest import assert_eq, fixture, load_fixture, test


@fixture(scope="session")
def base_config() -> Generator[dict[str, str]]:
    yield {"region": "us-east-1"}


@fixture
def client() -> Generator[dict[str, str]]:
    # Function fixture reusing the cached session fixture above.
    config = load_fixture(base_config())
    yield {"region": config["region"], "session": "open"}


@fixture
def request_scope() -> Generator[dict[str, str]]:
    conn = load_fixture(client())
    yield dict(conn)
    # `client` is still alive here: a depending fixture tears down before its
    # dependency, so teardown may use it.
    assert_eq(conn["session"], "open")


@test(mark="fast")
def test_layered_fixtures() -> None:
    scope = load_fixture(request_scope())
    assert_eq(scope["region"], "us-east-1")
```

A session fixture that tries to load a function fixture is rejected:

<!-- snektest-doc: expect-fail -->
```python
from collections.abc import Generator

from snektest import fixture, load_fixture, test


@fixture
def temp_file() -> Generator[str]:
    yield "/tmp/scratch"


@fixture(scope="session")
def cache() -> Generator[dict[str, str]]:
    # FixtureError: a session fixture cannot depend on a function fixture.
    path = load_fixture(temp_file())
    yield {"path": path}


@test(mark="fast")
def test_session_cannot_use_function_fixture() -> None:
    _ = load_fixture(cache())
```

Load fixtures at the beginning of each test, before actions or assertions. This
keeps fixture setup unconditional, makes teardown ownership obvious, and avoids
hiding fixture setup behind an earlier assertion failure or branch. Only load a
fixture later in a test when delayed fixture loading is the behavior being tested.

### Rich Assertions

Get helpful error messages with custom assertions:

<!-- snektest-doc: expect-fail -->
```python
from snektest import assert_eq, test


@test(mark="fast")
def test_show_dict_diff() -> None:
    assert_eq({"name": "alice", "age": 30}, {"name": "bob", "age": 30})
```

```text
E       {'name': 'alice', 'age': 30} != {'name': 'bob', 'age': 30}

E       - {'age': 30, 'name': 'bob'}
E       ?                      ^^^

E       + {'age': 30, 'name': 'alice'}
E       ?                      ^^^^^
```

<!-- snektest-doc: expect-fail -->
```python
from snektest import assert_in, test


@test(mark="fast")
def test_show_in_assertion() -> None:
    assert_in("qux", ["foo", "bar", "baz"])
```

```text
E       'qux' not found in ['foo', 'bar', 'baz']
```

### Async Support

Write async tests as naturally as sync ones:

```python
import asyncio
import time

from snektest import assert_eq, test


@test(mark="fast")
def test_sync_operation() -> None:
    time.sleep(0.1)
    result = "completed"
    assert_eq(result, "completed")


@test(mark="fast")
async def test_async_operation() -> None:
    await asyncio.sleep(0.1)
    result = "completed"
    assert_eq(result, "completed")
```

Async tests fail if they finish with tasks they created still pending. Snektest
cancels and awaits those tasks so they cannot contaminate later tests. Await
background work before returning from a test.

### Performance Benchmarks

Use `assert_benchmark(median_below=..., p95_below=...)` to assert the typical
and/or tail latency of a sync or async operation. At least one budget is
required. Put one-time setup before the context, then loop the timed operation
over `timing.rounds`. Snektest discards warmups and reports min, median,
nearest-rank p95, mean, and population standard deviation. Durations and budgets
are seconds. GC is suspended during measured rounds by default; pass
`disable_gc=False` to retain normal GC behavior. Benchmark contexts cannot
overlap because concurrent regions would distort both timings and process-wide
GC state.

Pass `name=` when a test contains multiple timed regions. Names appear in the
console and JSON output and give each region a stable identity for external
result tracking.

```python
import asyncio

from snektest import assert_benchmark, test


@test(mark="fast")
def test_list_copy_latency() -> None:
    with assert_benchmark(
        name="list copy",
        median_below=0.01,
        median_regression_below=0.10,
        regression_noise_floor=0.000001,
        rounds=20,
        warmup=3,
    ) as timing:
        for _ in timing.rounds:
            _ = list(range(100))


@test(mark="fast")
async def test_async_checkpoint_latency() -> None:
    with assert_benchmark(
        name="async checkpoint", median_below=0.01, rounds=20, warmup=3
    ) as timing:
        for _ in timing.rounds:
            await asyncio.sleep(0)
```

Set only the statistic that should gate the test, or set both. Budgets are
checked with strict `<` on context exit, raising `AssertionFailure`.
Statistics remain readable afterwards as `timing.min_seconds`,
`timing.median_seconds`, `timing.p95_seconds`, `timing.mean_seconds`, and
`timing.stddev_seconds`. `--timeout` bounds a complete async test, not an
individual benchmark round; it cannot interrupt synchronous or CPU-bound work.

To catch changes relative to a stored median, set
`median_regression_below=` to the maximum fractional increase. For example,
`0.10` allows an increase below 10%. `regression_noise_floor=` adds an absolute
allowance in seconds for timings where a small fixed change would look like a
large percentage. The allowed increase is
`max(baseline median * median_regression_below, regression_noise_floor)`.

The observed median must be strictly below the baseline plus that allowance.
Regression-gated regions require a unique `name=`. The relative gate is active
only in baseline comparison mode; ordinary runs still enforce the existing
absolute `median_below` and `p95_below` budgets.

Create or update a machine-bound snapshot after a passing run, then compare:

```bash
snektest --update-benchmark-baseline .snektest-benchmarks.json tests/performance
snektest --benchmark-baseline .snektest-benchmarks.json tests/performance
```

Only regions with `median_regression_below=` are stored. The identity combines
the path relative to the nearest `pyproject.toml`, test function, parameter case,
and region name. Changing rounds, warmup, or `disable_gc` requires an update.
A filtered update replaces matching entries and preserves unselected entries;
a `--mark` update conservatively replaces only tests it observed. Snektest never
writes a baseline when the run, fixture teardown, or collection fails.
Comparison ignores stored entries outside the current run. A renamed opted-in
region therefore fails as a missing current baseline; an update over the old and
new scope removes the stale identity and records the new one.
Updates use a sidecar lock and atomic replacement. Concurrent writers fail
instead of overwriting one another; if a process is killed during an update,
remove the reported stale `.lock` file before retrying.

The JSON file records the Python implementation/version, operating system,
architecture, CPU model, and logical CPU count. Comparison and update refuse a
machine mismatch. This metadata detects incompatible environments; it does not
normalize timings. On a shared hosted CI pool, generate the baseline from the
base branch and compare the proposed change earlier and later in the same job,
using a file outside the checkout so it survives the branch switch. Commit raw
snapshots only when a dedicated runner keeps the machine class fixed. Stored
p95 regression gates, history, statistical significance tests, and cross-machine
normalization remain out of scope.

For example, a pull-request job can use a second worktree while keeping both runs
on one allocated machine:

```bash
git fetch origin "$GITHUB_BASE_REF"
git worktree add "$RUNNER_TEMP/snektest-base" "origin/$GITHUB_BASE_REF"
(cd "$RUNNER_TEMP/snektest-base" && uv run snektest \
  --update-benchmark-baseline "$RUNNER_TEMP/snektest-benchmarks.json" \
  tests/performance)
uv run snektest \
  --benchmark-baseline "$RUNNER_TEMP/snektest-benchmarks.json" \
  tests/performance
```

A budgetless call is rejected by the type checker:

<!-- snektest-doc: expect-type-error=no-matching-overload, skip-run -->
```python
from snektest import assert_benchmark, test


@test(mark="fast")
def test_needs_a_timing_budget() -> None:
    with assert_benchmark():
        pass
```

### Parameterized Tests

Run the same test with different inputs:

```python
from snektest import Param, assert_eq, test

@test(
    [
        Param(value="hello", name="lowercase"),
        Param(value="WORLD", name="uppercase"),
        Param(value="MiXeD", name="mixed"),
    ],
    mark="fast",
)
def test_string_length(value: str) -> None:
    assert_eq(len(value), 5)

# Test with multiple parameter combinations (cartesian product)
@test(
    [Param(value="hello", name="hello"), Param(value="hi", name="hi")],
    [Param(value=" world", name="world"), Param(value=" there", name="there")],
    mark="fast",
)
def test_concatenation(greeting: str, target: str) -> None:
    result = greeting + target
    assert_eq(result[0], greeting[0])
```

### Static Type Checking

Snektest's public decorators and helpers are typed so test parameters, fixtures,
and Hypothesis strategies can be checked by `ty`.

## Running Tests

```sh
# Run all tests
snektest

# Run specific file
snektest tests/test_myfeature.py

# Run specific test
snektest tests/test_myfeature.py::test_something
# If an explicit test name or parameter case is not found, snektest exits with an error.

# Run tests with a marker
snektest --mark fast

# Run in four persistent execution workers (plus one fixture host)
snektest --workers 4

# Choose the lesser of available process CPUs and selected cases
snektest -n auto

# Override the default 60-second async-test timeout
snektest --timeout 5

# Disable the default timeout
snektest --no-timeout

# Disable stdout/stderr capture
snektest -s

# Print machine-readable JSON summary
snektest --json-output

# Print AI-agent usage guide
snektest --agent-docs
python -m snektest --agent-docs

# List or print bundled examples
snektest --examples
snektest --example async

# Drop into post-mortem debugging on first failure
snektest --pdb

# Run with coverage.py
coverage run -m snektest
```

Recursive directory discovery excludes files ignored by Git, including generated
test-shaped files under ignored output directories. An explicitly named test file
still runs. Outside a Git worktree, snektest checks every matching `test_*.py` file.

Human-readable summary lines are compact: exception details keep only the first
line and long lines may be truncated with an ellipsis. Full failure details and
tracebacks are printed earlier in the output. Use `--json-output` for a pure
machine-readable summary with per-test exception messages.

When `--pdb` is set, snektest enters a post-mortem debugger on the first test
failure or fixture error (setup/teardown), and stops executing further tests.
`--pdb` cannot be combined with explicit worker mode; rerun without `--workers`
to debug locally. `-s --json-output` is also rejected so JSON stdout remains one
document.

Pass `mutex="name"` to `@test()` or `@test_hypothesis()` when selected cases use
the same command-local resource. Mutex names are exact, non-empty, trimmed, and
case-sensitive. Same-name tests never overlap, while the scheduler can skip a
blocked case and run unrelated work. A mutex is not an OS lock and does not
coordinate other snektest commands or leaked subprocesses.

The CLI applies a 60-second timeout to every async test by default. Use
`--timeout SECONDS` to override it or `--no-timeout` to disable it. The timeout
is best-effort: it only fires while a test is suspended on an `await`, so a hung
`await` is reported as an error and the run continues, but a test stuck in
synchronous or CPU-bound work cannot be interrupted. A timed-out test still
runs its function-fixture teardown.

Interactions to know about:

- **`@test_hypothesis`.** For an async property test, the whole Hypothesis run
  (every example) executes inside one `await asyncio.to_thread(...)`, so
  the timeout bounds the *entire* property run, not each example. Worse, when it
  fires the worker thread running Hypothesis keeps going in the background — a
  thread can't be cancelled — so a runaway property test is reported as timed out
  but still consumes CPU until it finishes on its own. Sync property tests never
  yield to the loop and so are not bounded at all. For per-example limits, use
  Hypothesis's own `deadline`/`max_examples`; use `--no-timeout` if the complete
  property run legitimately needs to remain unbounded.
- **`--pdb`.** A timed-out test surfaces as a normal error, so `--pdb` will open a
  post-mortem on it. By the time the timeout fires the test's own `await` frame has
  already been unwound by cancellation, so the debugger lands on snektest's
  internal timeout machinery, not the line in your test that hung. `--pdb` is of
  limited help for locating a timeout; use it for ordinary failures.

## Execution Model

Snektest completes deterministic collection before any test starts. Omit
`--workers` for in-process sequential execution on one event loop. Explicit
worker mode starts the requested number of persistent spawn workers, capped by
the selected case count, plus one canonical collector/run-fixture host;
`--workers 1` still uses real child processes. Each worker executes one test at
a time on its own event loop and owns its session fixtures. Results are presented
in canonical manifest order even when workers finish out of order. Repeated
filters remain repeated invocations with distinct ordinals.

Teardown is dependency-first-in-reverse: function fixtures after each test,
session fixtures when each worker exits, then run fixtures in the host. A worker
replacement after a crash is a new process incarnation and may set up session
fixtures again.

## Marking Tests

Use the `mark` argument on `@test()` to attach built-in marker metadata for filtering. Marking tests is the recommended way to use snektest: every test should declare whether it is `"fast"`, `"medium"`, or `"slow"`.

`Marker` is a type alias for those three literal strings. Markers must be passed as a single marker literal.

Markers describe the resources a test may use, not how long it is expected to
take:

`fast` means the test runs entirely in memory, without IO, threads, or
subprocesses.

`medium` means the test may use local IO or threads, but not network IO or
subprocesses.

`slow` means the test may use network IO, subprocesses, or other expensive
external resources.

```python
from snektest import test

@test(mark="slow")
def test_integration() -> None:
    pass

@test(mark="fast")
def test_unit() -> None:
    pass
```

Use `--mark fast`, `--mark medium`, or `--mark slow` to run one marker group.

## Property-Based Testing with Hypothesis

Snektest provides first-class integration with [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing. Property-based tests automatically generate test cases to explore edge cases and verify properties that should hold for all inputs.

### Basic Usage

Use the `@test_hypothesis(..., mark=...)` decorator with Hypothesis strategies to automatically generate marked test inputs:

```python
from hypothesis import strategies as st
from snektest import assert_ge, test_hypothesis

@test_hypothesis(st.integers(), mark="fast")
async def test_absolute_value_is_non_negative(x: int) -> None:
    result = abs(x)
    assert_ge(result, 0)
```

### Multiple Strategies

Pass multiple strategies for functions with multiple parameters:

```python
from hypothesis import strategies as st
from snektest import assert_eq, test_hypothesis

@test_hypothesis(st.text(), st.text(), mark="fast")
async def test_string_concatenation_length(s1: str, s2: str) -> None:
    result = s1 + s2
    assert_eq(len(result), len(s1) + len(s2))
```

### Async Function Support

Property-based tests work seamlessly with async functions. Snektest automatically handles the complexity of running Hypothesis by executing the Hypothesis engine in a worker thread and scheduling each generated test case back onto the main event loop:

```python
import asyncio
from hypothesis import strategies as st
from snektest import assert_eq, assert_true, test_hypothesis

@test_hypothesis(st.integers(min_value=0, max_value=100), mark="fast")
async def test_async_computation(n: int) -> None:
    # Simulate async operation
    await asyncio.sleep(0.001)
    result = n * 2
    assert_true(result >= 0)
    assert_eq(result % 2, 0)
```

### Configuring Hypothesis

Use Hypothesis's `@settings()` decorator to configure test behavior. Apply it above or below `@test_hypothesis()`:

```python
from hypothesis import settings, strategies as st
from snektest import assert_eq, test_hypothesis

@settings(max_examples=500, deadline=None)
@test_hypothesis(st.lists(st.integers()), mark="fast")
async def test_list_operations(numbers: list[int]) -> None:
    reversed_twice = list(reversed(list(reversed(numbers))))
    assert_eq(reversed_twice, numbers)
```

### Type Safety

The decorator provides full type safety - strategy types are checked against function parameters:

<!-- snektest-doc: expect-type-error=invalid-argument-type@10, skip-run -->
```python
from hypothesis import strategies as st
from snektest import test_hypothesis

# ✓ This type-checks correctly
@test_hypothesis(st.integers(), st.text(), mark="fast")
async def test_correct_types(x: int, s: str) -> None:
    pass

# ✗ This will fail type checking - int strategy doesn't match str parameter
@test_hypothesis(st.integers(), mark="fast")
async def test_wrong_type(x: str) -> None:  # Type error!
    pass
```

### Combining with Traditional Tests

You can mix property-based tests with traditional example-based tests in the same file:

```python
from hypothesis import strategies as st
from snektest import Param, assert_eq, test, test_hypothesis

# Property-based test
@test_hypothesis(st.integers(), st.integers(), mark="fast")
async def test_addition_commutative(a: int, b: int) -> None:
    assert_eq(a + b, b + a)

# Traditional parameterized test
@test(
    [
        Param(value=(2, 3, 5), name="small"),
        Param(value=(100, 200, 300), name="large"),
    ],
    mark="fast",
)
async def test_addition_specific_cases(values: tuple[int, int, int]) -> None:
    a, b, expected = values
    assert_eq(a + b, expected)
```

## OpenAPI Contract Testing

Install `snektest[schema]` to generate contract tests from an OpenAPI JSON or
YAML file. `@test_schema` collects one test per operation, generates
schema-compliant requests with Hypothesis, and checks that each response is not
a server error and conforms to its declared response schema.

The decorated function is declarative: its body is not called. It supplies the
test name, marker, and any `@hypothesis.settings`. A literal target is enough for
an already-running service:

<!-- snektest-doc: skip-run -->
```python
from hypothesis import settings

from snektest import test_schema


@settings(max_examples=50, deadline=None)
@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    headers={"Authorization": "Bearer test-token"},
    request_timeout=5.0,
    mark="slow",
)
async def test_api_contract() -> None:
    ...
```

`base_url` and `headers` may also be fixture handles. This lets a session fixture
start a service on an ephemeral port before Schemathesis sends requests:

<!-- snektest-doc: skip-run -->
```python
from collections.abc import AsyncGenerator

from snektest import fixture, test_schema


@fixture(scope="session")
async def api_url() -> AsyncGenerator[str]:
    # Start the service here and tear it down after yield.
    yield "http://127.0.0.1:8123"


@test_schema("openapi.json", base_url=api_url(), mark="slow")
async def test_api_contract() -> None:
    ...
```

Operation names are regular parameter-case names, such as
`test_api_contract[GET /users/{user_id}]`, so they can be selected directly from
the CLI.

For login and token-refresh flows, pass a native Schemathesis auth provider.
Schemathesis calls `get` in the worker thread, caches its result for five minutes,
and uses `set` to modify generated cases. Return `None` from `get` to skip auth
for an operation. Static credentials should continue to use `headers`.

Custom checks are native Schemathesis check functions. They run in addition to
Snektest's server-error and response-schema checks and should raise
`AssertionError` when an invariant is violated:

<!-- snektest-doc: skip-run -->
```python
from collections.abc import Mapping
from typing import Protocol

from snektest import test_schema


class AuthCase(Protocol):
    headers: dict[str, str] | None


class ContractResponse(Protocol):
    headers: Mapping[str, object]


class TokenAuth:
    def get(self, case: AuthCase, context: object) -> str:
        # A real provider may log in or refresh a token here.
        return "test-token"

    def set(self, case: AuthCase, data: str, context: object) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"


def require_request_id(
    context: object,
    response: ContractResponse,
    case: object,
) -> None:
    if "x-request-id" not in response.headers:
        raise AssertionError("response is missing X-Request-ID")


@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    auth=TokenAuth,
    checks=[require_request_id],
    mark="slow",
)
async def test_authenticated_contract() -> None:
    ...
```

Use `@test_schema_workflow` to exercise explicit OpenAPI links and inferred
producer-consumer relationships. A workflow is one Snektest result because
Hypothesis generates and shrinks the whole operation sequence together:

<!-- snektest-doc: skip-run -->
```python
from hypothesis import settings

from snektest import test_schema_workflow


@settings(max_examples=50, stateful_step_count=8, deadline=None)
@test_schema_workflow(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    mark="slow",
)
async def test_api_workflows() -> None:
    ...
```

Stateful workflows accept the same `headers`, `auth`, `checks`, timeout, fixture,
and Hypothesis settings as operation tests. Negative workflow generation and
GraphQL remain outside this integration. A schema without usable links fails during
collection with guidance; malformed links identify the source operation,
response status, link name, and missing or invalid target. Workflow failures
include the minimized method, path, and response-status sequence in both console
and JSON output. Credentials, query values, and request bodies are omitted from
that sequence.

### Negative Requests

Set `generation="negative"` on `@test_schema` to generate requests that
deliberately violate request schemas. A response passes only when its status is
an allowed 4xx and that status is documented for the operation. A 2xx means the
API accepted invalid input; a 5xx remains a server-error failure.

<!-- snektest-doc: skip-run -->
```python
from hypothesis import settings

from snektest import SchemaFilter, SchemaOperationSelector, test_schema


@settings(max_examples=100, deadline=None)
@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    generation="negative",
    expected_statuses={400, 422},
    operations=SchemaFilter(
        exclude=(SchemaOperationSelector(path="/health"),)
    ),
    mark="slow",
)
async def test_invalid_requests() -> None:
    ...
```

`expected_statuses` defaults to every 4xx status and must be a non-empty
collection containing only 4xx integers. Response-schema checks, auth, custom
checks, operation filters, fixtures, and Hypothesis shrinking continue to apply.
Negative cases are named like
`test_invalid_requests[negative POST /users]`. Operations without request
constraints cannot produce negative cases and report an error suggesting that
they be removed with `SchemaFilter`.

### Operation Filtering

Use `SchemaFilter` to keep destructive, privileged, deprecated, or unsupported
operations out of a contract suite. Selector fields are exact matches combined
with AND; multiple selectors are OR alternatives. Excludes always win over
includes, and HTTP methods are case-insensitive:

<!-- snektest-doc: skip-run -->
```python
from snektest import SchemaFilter, SchemaOperationSelector, test_schema


public_operations = SchemaFilter(
    include=(
        SchemaOperationSelector(tag="public"),
        SchemaOperationSelector(path="/health", method="GET"),
    ),
    exclude=(
        SchemaOperationSelector(operation_id="deleteAllUsers"),
    ),
    exclude_deprecated=True,
)


@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    operations=public_operations,
    mark="slow",
)
async def test_public_contract() -> None:
    ...
```

The same `operations=` filter works with `@test_schema_workflow`. Both the
producer and consumer must remain selected for a link to be exercised. Selecting
no operations, or removing one end of every workflow link, fails collection with
an actionable error instead of running an empty suite.

## Assertions Reference

All assertion functions are importable from `snektest` and accept an optional
`msg` keyword argument for custom error messages.

Assertion argument order is intentional. Pass the observed/computed value first
and the expected/reference value second, following the parameter names in each
signature: `assert_eq(actual, expected)`, `assert_in(member, container)`,
`assert_isinstance(obj, classinfo)`, and `assert_len(obj, expected_length)`.

### Value and Comparison Assertions

- `assert_eq(actual, expected)` — assert that `actual == expected`
- `assert_ne(actual, expected)` — assert that `actual != expected`
- `assert_true(value)` — assert that `value is True`
- `assert_false(value)` — assert that `value is False`
- `assert_is_none(value)` — assert that `value is None`
- `assert_is_not_none(value)` — assert that `value is not None`; returns `value` narrowed to its non-`None` type
- `assert_is(actual, expected)` — assert that `actual is expected`
- `assert_is_not(actual, expected)` — assert that `actual is not expected`
- `assert_lt(actual, expected)` — assert that `actual < expected`
- `assert_gt(actual, expected)` — assert that `actual > expected`
- `assert_le(actual, expected)` — assert that `actual <= expected`
- `assert_ge(actual, expected)` — assert that `actual >= expected`
- `assert_in(member, container)` — assert that `member in container`
- `assert_not_in(member, container)` — assert that `member not in container`
- `assert_isinstance(obj, classinfo)` — assert that `isinstance(obj, classinfo)` is true; `classinfo` may be a tuple of types. When `classinfo` is a single type, returns `obj` narrowed to that type (bind it to narrow for later use; discard with `_ =` for a pure assertion)
- `assert_not_isinstance(obj, classinfo)` — assert that `isinstance(obj, classinfo)` is false
- `assert_len(obj, expected_length)` — assert that `len(obj) == expected_length`

### Exception Assertions

**`assert_raises(*expected_exceptions, msg=None)`** - Assert that code raises an expected exception

Use as a context manager to verify that a specific exception is raised:

```python
from snektest import assert_eq, assert_raises, test

@test(mark="fast")
def test_division_by_zero() -> None:
    with assert_raises(ZeroDivisionError):
        _ = 1 / 0  # ty: ignore[division-by-zero]

@test(mark="fast")
def test_multiple_exception_types() -> None:
    # Can accept multiple exception types
    with assert_raises(ValueError, TypeError):
        _ = int("not a number")

@test(mark="fast")
def test_access_exception() -> None:
    # Access the caught exception via the exception property
    with assert_raises(ValueError) as exc_info:
        raise ValueError("custom message")

    assert_eq(exc_info.exception.args[0], "custom message")
```

### Memory Assertions

**`assert_memory(*, peak_below=None, slope_below=None, rounds=1, warmup=1, backend="tracemalloc")`**
— Assert on a region's memory behavior. Budgets are bytes-as-`int` (no size
strings). At least one of `peak_below` (max transient allocation) or
`slope_below` (per-round leak growth) is required; a budgetless call is a type
error. Budgets are checked on exit, raising `AssertionFailure`.

For a whole-block peak budget, wrap the region directly. For leak detection,
loop your work over `m.rounds` — a stateful iterator running `warmup + rounds`
iterations; a `slope_below` budget needs `rounds >= 10`. The slope is a
Theil–Sen fit of retained bytes per round (resistant to a single-round GC
spike), and `peak_bytes` is the max single-round peak. `m.peak_bytes` and
`m.growth_slope` stay readable after the block for custom assertions. On a pass,
the measured numbers are shown on the result line (e.g.
`peak=1.0MB (<8.0MB)`). `assert_memory` cannot be nested.

```python
from snektest import assert_memory, test


@test(mark="fast")
def test_peak_allocation_budget() -> None:
    with assert_memory(peak_below=8 * 1024 * 1024):
        payload = bytearray(1024 * 1024)
        del payload


@test(mark="fast")
def test_no_leak_across_rounds() -> None:
    scratch: list[bytearray] = []
    with assert_memory(slope_below=64 * 1024, rounds=20) as m:
        for _ in m.rounds:
            scratch.clear()
            scratch.append(bytearray(32 * 1024))
    _ = m.peak_bytes
    _ = m.growth_slope
```

A budgetless call is rejected by the type checker:

<!-- snektest-doc: expect-type-error=no-matching-overload, skip-run -->
```python
from snektest import assert_memory, test


@test(mark="fast")
def test_needs_a_budget() -> None:
    # Type error: no overload matches a call with neither budget set.
    with assert_memory():
        pass
```

### Unconditional Failure

**`fail(msg=None)`** - Raise an AssertionFailure unconditionally

<!-- snektest-doc: expect-fail -->
```python
from snektest import fail, test

@test(mark="fast")
def test_unreachable() -> None:
    if False:
        pass
    else:
        fail("This code path should never execute")
```
