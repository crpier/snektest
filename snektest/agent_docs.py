"""Embedded documentation and examples for coding agents."""

from importlib.resources import files

from snektest.models import BadRequestError

AGENT_DOCS = """# snektest agent guide

Snektest is a Python testing framework with first-class async and typing support.

## Quick start

Create a `test_*.py` file and decorate test functions with `@test(mark=...)`. Mark every test with the resources it may use:

```python
from snektest import assert_eq, test

@test(mark="fast")
def test_addition() -> None:
    assert_eq(1 + 1, 2)
```

Run tests with:

```bash
snektest
python -m snektest
```

Useful commands:

```bash
snektest --help
snektest --agent-docs
snektest --examples
snektest --example async
snektest examples
snektest example async
```

## Core patterns

- Import assertions from `snektest`; prefer `assert_eq()` over bare `assert`.
- Mark every test. This is the recommended way to use snektest.
- Use `mark="fast"` for in-memory tests with no IO, threads, or subprocesses.
- Use `mark="medium"` for tests that use local IO or threads.
- Use `mark="slow"` for tests that use network IO, subprocesses, or expensive external resources.
- Async tests are regular `async def` functions decorated with `@test(mark=...)`.
- Use `Param(value=..., name=...)` inside `@test([...], mark=...)` for parameterization.
- Use generator fixtures with `load_fixture(fixture())`.
- Use `@session_fixture()` for fixture setup shared by the whole test run.
- Filter runs with paths such as `snektest tests/test_math.py::test_addition` or markers such as `snektest --mark fast`.

## Copyable examples

List bundled examples:

```bash
snektest --examples
```

Print one example:

```bash
snektest --example basic
snektest --example fixtures
snektest --example async
snektest --example parametrize
```
"""

EXAMPLE_FILES: dict[str, str] = {
    "async": "async_tests.py",
    "basic": "basic_test.py",
    "fixtures": "fixtures.py",
    "parametrize": "parametrize.py",
}


def get_agent_docs() -> str:
    """Return the embedded guide for AI agents and humans."""
    return AGENT_DOCS


def get_examples_listing() -> str:
    """Return a human-readable list of bundled examples."""
    lines = [
        "Bundled snektest examples:",
        *[f"  {name:<12} snektest --example {name}" for name in sorted(EXAMPLE_FILES)],
    ]
    return "\n".join(lines) + "\n"


def get_example_source(example_name: str) -> str:
    """Return the source code for a bundled example."""
    normalized_name = example_name.removesuffix(".py")
    file_name = EXAMPLE_FILES.get(normalized_name)
    if file_name is None:
        file_name = next(
            (
                candidate
                for candidate in EXAMPLE_FILES.values()
                if candidate.removesuffix(".py") == normalized_name
            ),
            None,
        )
    if file_name is None:
        available = ", ".join(sorted(EXAMPLE_FILES))
        msg = f"Unknown example `{example_name}`. Use one of: {available}"
        raise BadRequestError(msg)

    resource = files("snektest.examples").joinpath(file_name)
    return resource.read_text(encoding="utf-8")
