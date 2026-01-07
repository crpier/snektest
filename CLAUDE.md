# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

snektest is a Python testing framework with first class support for async and static typing.

## Development Commands

### Running Tests
```bash
# Run all tests
uv run snektest

# Run with verbose logging
uv run snektest -v    # INFO level
uv run snektest -vv   # DEBUG level

# Run specific test file
uv run snektest tests/integration/test_basic.py

# Run specific test function
uv run snektest tests/integration/test_basic.py::test_no_params

# Run specific parameterized test
uv run snektest tests/integration/test_basic.py::test_1_params[ascii name]
```

### Type Checking & Linting
```bash
# Type check
uv run pyright

# Lint/format
uv run ruff check
uv run ruff format --check
```

### Package Management
This project uses `uv` for dependency management. The project requires Python >=3.14.

## Architecture

### Test Collection & Execution Flow

1. **CLI Entry** (`cli.py:main`): Parse args, create filter items, start async event loop
2. **Producer-Consumer Pattern**:
   - Producer thread (`load_tests_from_filters`) walks filesystem, imports test modules, adds tests to async queue
   - Consumer coroutine (`run_tests`) executes tests from queue concurrently
3. **Test Discovery** (`load_tests_from_file`): Import modules, find functions decorated with `@test()`, expand parameterized tests
4. **Test Execution** (`execute_test`): Capture stdout/stderr, execute test function (sync or async), teardown function fixtures, return `TestResult`

### Fixture System

Two fixture scopes with generator-based setup/teardown:

- **Function fixtures**: Created via `load_fixture()` directly in tests. Stored in `_FUNCTION_FIXTURES` list. Torn down after each test in reverse order.
- **Session fixtures**: Decorated with `@session_fixture()`. Registered in `_SESSION_FIXTURES` dict keyed by code object. Created on first `load_fixture()` call, reused across tests, torn down after all tests complete.

Both use generators/async generators with yield for setup/teardown:
```python
async def my_fixture() -> AsyncGenerator[str]:
    # setup
    yield "value"
    # teardown
```

### Parameterization

Tests can accept multiple parameter sets via `@test(param_list1, param_list2)`. The `Param.to_dict()` creates all combinations using `itertools.product`, keyed by param names. Each combination becomes a separate test execution.

### Assertions

Custom assertion system with rich error reporting. Use `assert_eq()` from `snektest.assertions` rather than bare `assert`. Raises `AssertionFailure` with actual/expected values for better error messages.

### Type Checking Configuration

Extremely strict pyright configuration (all checks set to "error"). When adding new code, expect to fully type-annotate everything. See pyproject.toml:69-174 for complete settings.

Notable exceptions to pyright rules:
- `reportIncompatibleVariableOverride = false`: Allow subclasses to override with different types
- `reportMissingSuperCall = false`: Don't require calling parent methods
- `reportImplicitOverride = false`: Don't require explicit `@override` decorator

### Code Style Notes

- Ruff with extensive rules enabled (see pyproject.toml:22-64)
- Tests allow magic numbers, assert statements, private access (see per-file-ignores)
- Line length 88, but E501 ignored (long strings for messages okay)
- Mixed case names allowed in `annotations.py` for validators like `validate_SomeType`
