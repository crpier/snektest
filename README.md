# snektest

A type-safe, async-native Python testing framework.

## Installation

```bash
uv add snektest
```

## Type checking is part of the contract

snektest expects your test code to pass a strict static type checker (such as
pyright) before tests run — typically continuously, via your editor's language
server. The API is designed around this: signatures are exact, and snektest
does not re-validate at runtime what a type checker already rejects. If you
skip type checking, misuse that a checker would flag — such as applying
`@test` without parentheses — can fail silently at runtime.

Runtime validation is reserved for what static checkers cannot see: CLI
input, file paths, and fixture protocol rules (for example, session fixtures
must not accept parameters).

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
is set up once and reused across the run. Calling a decorated fixture returns a
handle; pass it to `load_fixture()`. Fixtures may take arguments, passed at the
call site (e.g. `load_fixture(make_user("Ada"))`), and calling one twice yields
two independent instances. Session fixtures must not accept parameters because
they are cached once per fixture function. Use a function fixture for
parameter-dependent setup, or have a zero-argument session fixture return a
factory/cache.

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

- A **function** fixture may depend on a function fixture or a session fixture.
- A **session** fixture may depend on another session fixture.
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
  holds for both function and session scope.

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
and Hypothesis strategies can be checked by tools such as pyright.

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

# Fail any async test that runs longer than N seconds
snektest --timeout 5

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

Human-readable summary lines are compact: exception details keep only the first
line and long lines may be truncated with an ellipsis. Full failure details and
tracebacks are printed earlier in the output. Use `--json-output` for a pure
machine-readable summary with per-test exception messages.

When `--pdb` is set, snektest enters a post-mortem debugger on the first test
failure or fixture error (setup/teardown), and stops executing further tests.

`--timeout` sets a run-wide ceiling, in seconds, on each test. It is async-only
and best-effort: the timeout only fires while a test is suspended on an `await`,
so a hung `await` is reported as an error and the run continues, but a test
stuck in synchronous or CPU-bound work cannot be interrupted. A timed-out test
still runs its function-fixture teardown.

Interactions to know about:

- **`@test_hypothesis`.** For an async property test, the whole Hypothesis run
  (every example) executes inside one `await asyncio.to_thread(...)`, so
  `--timeout` bounds the *entire* property run, not each example. Worse, when it
  fires the worker thread running Hypothesis keeps going in the background — a
  thread can't be cancelled — so a runaway property test is reported as timed out
  but still consumes CPU until it finishes on its own. Sync property tests never
  yield to the loop and so are not bounded at all. For per-example limits, use
  Hypothesis's own `deadline`/`max_examples` instead of `--timeout`.
- **`--pdb`.** A timed-out test surfaces as a normal error, so `--pdb` will open a
  post-mortem on it. By the time the timeout fires the test's own `await` frame has
  already been unwound by cancellation, so the debugger lands on snektest's
  internal timeout machinery, not the line in your test that hung. `--pdb` is of
  limited help for locating a timeout; use it for ordinary failures.

## Execution Model

Tests run sequentially — one at a time, in collection order — on a single
asyncio event loop shared by the entire run. An async test is awaited to
completion before the next test starts, so tests never interleave with each
other; a background task a test starts but does not await can, however, keep
running on the shared loop while later tests execute.

Test collection runs in a background thread while tests execute, so a test
module may be imported while tests from earlier modules are already running.
Avoid import-time side effects in test modules.

Teardown is last-in-first-out: function fixtures are torn down after each test
in reverse loading order, and session fixtures are torn down after all tests
finish in reverse registration order.

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

<!-- snektest-doc: expect-type-error, skip-run -->
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
        _ = 1 / 0

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
