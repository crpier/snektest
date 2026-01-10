# snektest

A type-safe, async-native Python testing framework.

## Installation

```bash
uv add snektest
```

## Quick Start

Create a `test_*.py` file:

```python
from collections.abc import AsyncGenerator

from snektest import test, load_fixture
from snektest.assertions import assert_eq

async def provide_number() -> AsyncGenerator[int, None]:
    yield 2

@test()
async def test_basic_math():
    given_number = await load_fixture(provide_number())
    result = given_number * 2
    assert_eq(result, 4)

@test()
def test_strings():
    assert_eq("hello".upper(), "HELLO")
```

Run your tests:

```bash
snektest
```

## Features

### Session Fixtures

Set up and tear down test dependencies with session-scoped fixtures:

```python
from collections.abc import AsyncGenerator
from snektest import test, session_fixture, load_fixture
from snektest.assertions import assert_eq

@session_fixture()
async def connection_pool() -> AsyncGenerator[dict[str, str], None]:
    # Setup: runs once for all tests
    pool = {"host": "localhost", "status": "connected"}
    yield pool
    # Teardown: runs after all tests
    pool["status"] = "disconnected"

@test()
async def test_connection():
    pool = await load_fixture(connection_pool())
    assert_eq(pool["status"], "connected")
```

### Rich Assertions

Get helpful error messages with custom assertions:

```python
from snektest import assert_eq, test


@test()
def test_show_dict_diff() -> None:
    assert_eq({"name": "alice", "age": 30}, {"name": "bob", "age": 30})
```

```text
E       AssertionError: {'name': 'alice', 'age': 30} != {'name': 'bob', 'age': 30}

E       - {'age': 30, 'name': 'bob'}
E       ?                      ^^^

E       + {'age': 30, 'name': 'alice'}
E       ?                      ^^^^^
```

```python
from snektest import assert_in, test


@test()
def test_show_in_assertion() -> None:
    assert_in("qux", ["foo", "bar", "baz"])
```

```text
E       AssertionError: 'qux' not found in ['foo', 'bar', 'baz']
E       'qux' in ['foo', 'bar', 'baz']
```

### Async Support

Write async tests as naturally as sync ones:

```python
import asyncio
import time

from snektest import test
from snektest.assertions import assert_eq


@test()
def test_sync_operation():
    time.sleep(0.1)
    result = "completed"
    assert_eq(result, "completed")


@test()
async def test_async_operation():
    await asyncio.sleep(0.1)
    result = "completed"
    assert_eq(result, "completed")
```

### Parameterized Tests

Run the same test with different inputs:

```python
from snektest import test, Param
from snektest.assertions import assert_eq

@test([
    Param(value="hello", name="lowercase"),
    Param(value="WORLD", name="uppercase"),
    Param(value="MiXeD", name="mixed"),
])
def test_string_length(value: str):
    assert_eq(len(value), 5)

# Test with multiple parameter combinations (cartesian product)
@test(
    [Param(value="hello", name="hello"), Param(value="hi", name="hi")],
    [Param(value=" world", name="world"), Param(value=" there", name="there")],
)
def test_concatenation(greeting: str, target: str):
    result = greeting + target
    assert_eq(result[0], greeting[0])
```

### Static type checking

...

## Running Tests

```sh
# Run all tests
snektest

# Run specific file
snektest tests/test_myfeature.py

# Run specific test
snektest tests/test_myfeature.py::test_something
```

## Assertions Reference

All assertion functions accept an optional `msg` keyword argument for custom error messages.

### Equality and Inequality

**`assert_eq(actual, expected, *, msg=None)`** - Assert that `actual == expected`

```python
from snektest import test
from snektest.assertions import assert_eq

@test()
def test_equality():
    assert_eq(2 + 2, 4)
    assert_eq("hello", "hello")
    assert_eq([1, 2, 3], [1, 2, 3])
```

**`assert_ne(actual, expected, *, msg=None)`** - Assert that `actual != expected`

```python
from snektest import test
from snektest.assertions import assert_ne

@test()
def test_inequality():
    assert_ne(2 + 2, 5)
    assert_ne("hello", "world")
```

### Boolean Values

**`assert_true(value, *, msg=None)`** - Assert that `value is True`

```python
from snektest import test
from snektest.assertions import assert_true

@test()
def test_true():
    assert_true(5 > 3)
    assert_true(True)
```

**`assert_false(value, *, msg=None)`** - Assert that `value is False`

```python
from snektest import test
from snektest.assertions import assert_false

@test()
def test_false():
    assert_false(5 < 3)
    assert_false(False)
```

### None Checks

**`assert_is_none(value, *, msg=None)`** - Assert that `value is None`

```python
from snektest import test
from snektest.assertions import assert_is_none

@test()
def test_none():
    result = None
    assert_is_none(result)
```

**`assert_is_not_none(value, *, msg=None)`** - Assert that `value is not None`

```python
from snektest import test
from snektest.assertions import assert_is_not_none

@test()
def test_not_none():
    result = "something"
    assert_is_not_none(result)
```

### Identity Checks

**`assert_is(actual, expected, *, msg=None)`** - Assert that `actual is expected`

```python
from snektest import test
from snektest.assertions import assert_is

@test()
def test_identity():
    a = [1, 2, 3]
    b = a
    assert_is(a, b)
```

**`assert_is_not(actual, expected, *, msg=None)`** - Assert that `actual is not expected`

```python
from snektest import test
from snektest.assertions import assert_is_not

@test()
def test_not_identity():
    a = [1, 2, 3]
    b = [1, 2, 3]
    assert_is_not(a, b)
```

### Comparisons

**`assert_lt(actual, expected, *, msg=None)`** - Assert that `actual < expected`

```python
from snektest import test
from snektest.assertions import assert_lt

@test()
def test_less_than():
    assert_lt(3, 5)
    assert_lt("a", "b")
```

**`assert_gt(actual, expected, *, msg=None)`** - Assert that `actual > expected`

```python
from snektest import test
from snektest.assertions import assert_gt

@test()
def test_greater_than():
    assert_gt(5, 3)
    assert_gt("b", "a")
```

**`assert_le(actual, expected, *, msg=None)`** - Assert that `actual <= expected`

```python
from snektest import test
from snektest.assertions import assert_le

@test()
def test_less_or_equal():
    assert_le(3, 5)
    assert_le(5, 5)
```

**`assert_ge(actual, expected, *, msg=None)`** - Assert that `actual >= expected`

```python
from snektest import test
from snektest.assertions import assert_ge

@test()
def test_greater_or_equal():
    assert_ge(5, 3)
    assert_ge(5, 5)
```

### Membership

**`assert_in(member, container, *, msg=None)`** - Assert that `member in container`

```python
from snektest import test
from snektest.assertions import assert_in

@test()
def test_membership():
    assert_in(2, [1, 2, 3])
    assert_in("hello", "hello world")
    assert_in("key", {"key": "value"})
```

**`assert_not_in(member, container, *, msg=None)`** - Assert that `member not in container`

```python
from snektest import test
from snektest.assertions import assert_not_in

@test()
def test_not_membership():
    assert_not_in(5, [1, 2, 3])
    assert_not_in("foo", "hello world")
```

### Type Checks

**`assert_isinstance(obj, classinfo, *, msg=None)`** - Assert that `isinstance(obj, classinfo) is True`

```python
from snektest import test
from snektest.assertions import assert_isinstance

@test()
def test_instance():
    assert_isinstance("hello", str)
    assert_isinstance(42, int)
    assert_isinstance([1, 2], list)
    assert_isinstance(5, (int, float))
```

**`assert_not_isinstance(obj, classinfo, *, msg=None)`** - Assert that `isinstance(obj, classinfo) is False`

```python
from snektest import test
from snektest.assertions import assert_not_isinstance

@test()
def test_not_instance():
    assert_not_isinstance("hello", int)
    assert_not_isinstance(42, str)
```

### Length

**`assert_len(obj, expected_length, *, msg=None)`** - Assert that `len(obj) == expected_length`

```python
from snektest import test
from snektest.assertions import assert_len

@test()
def test_length():
    assert_len([1, 2, 3], 3)
    assert_len("hello", 5)
    assert_len({"a": 1, "b": 2}, 2)
```

### Exception Assertions

**`assert_raises(*expected_exceptions, msg=None)`** - Assert that code raises an expected exception

Use as a context manager to verify that a specific exception is raised:

```python
from snektest import test, assert_raises, assert_eq

@test()
def test_division_by_zero():
    with assert_raises(ZeroDivisionError):
        1 / 0

@test()
def test_multiple_exception_types():
    # Can accept multiple exception types
    with assert_raises(ValueError, TypeError):
        int("not a number")

@test()
def test_access_exception():
    # Access the caught exception via the exception property
    with assert_raises(ValueError) as exc_info:
        raise ValueError("custom message")

    assert_eq(exc_info.exception.args[0], "custom message")
```

### Unconditional Failure

**`fail(msg=None)`** - Raise an AssertionFailure unconditionally

```python
from snektest import test, fail

@test()
def test_unreachable():
    if False:
        pass
    else:
        fail("This code path should never execute")
```
