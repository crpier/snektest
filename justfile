# Simple justfile for a few helper commands

# Run linter
check-lint:
    uv run ruff check .

check-fmt:
    uv run ruff format --check .

check-types:
    uv run pyright .

# All checks
check: check-lint check-fmt check-types

# Run tests
test:
    uv run snektest tests/
