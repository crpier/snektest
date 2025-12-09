# snektest

A Python testing framework with first-class support for async and static typing.

## Installation

```bash
pip install snektest
```

## Quick Start

Create a test file (any `.py` file in your project):

```python
from snektest import test
from snektest.assertions import assert_eq

@test()
async def test_basic_math():
    result = 2 + 2
    assert_eq(result, 4)

@test()
def test_strings():
    assert_eq("hello".upper(), "HELLO")
```

Run your tests:

```bash
snek
```

## Features

### Async Support

Write async tests as naturally as sync ones:

```python
@test()
async def test_async_operation():
    result = await some_async_function()
    assert_eq(result, "expected")
```

### Parameterized Tests

Run the same test with different inputs:

```python
from snektest import test, Param

@test(
    Param(value=1, expected=2),
    Param(value=5, expected=10),
    Param(value=0, expected=0),
)
def test_double(value: int, expected: int):
    assert_eq(value * 2, expected)
```

### Fixtures

Set up and tear down test dependencies with function or session-scoped fixtures:

```python
from snektest import test, session_fixture, load_fixture
from collections.abc import AsyncGenerator

@session_fixture()
async def database() -> AsyncGenerator[Database]:
    # Setup: runs once for all tests
    db = await Database.connect()
    yield db
    # Teardown: runs after all tests
    await db.close()

@test()
async def test_database_query():
    db = await load_fixture(database)
    result = await db.query("SELECT 1")
    assert_eq(result, 1)
```

### Rich Assertions

Get helpful error messages with custom assertions:

```python
from snektest.assertions import assert_eq, assert_true, assert_in

@test()
def test_with_assertions():
    assert_eq(actual_value, expected_value)
    assert_true(condition)
    assert_in(item, collection)
```

## Running Tests

```bash
# Run all tests
snek

# Run specific file
snek tests/test_myfeature.py

# Run specific test
snek tests/test_myfeature.py::test_something

# Verbose output
snek -v    # INFO level
snek -vv   # DEBUG level
```

## How It Works

snektest discovers tests by walking your project directory and finding functions decorated with `@test()`. Tests run concurrently by default, with a producer thread discovering tests while consumer coroutines execute them in parallel.

The fixture system uses Python generators to handle setup and teardown—code before `yield` runs before the test, code after `yield` runs after. Function fixtures are created fresh for each test, while session fixtures are shared across all tests for efficiency.

When a test fails, snektest captures rich context about what went wrong, including color-coded diffs for assertion failures and detailed tracebacks that help you quickly identify the issue.
