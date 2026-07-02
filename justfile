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

# Render console output across terminal shapes for presentation review.
# Pass snektest filters/flags through, e.g: just render tests/isolated/test_basic.py
render *ARGS:
    uv run python -m testutils.render_matrix {{ARGS}}

test:
    uv run coverage erase
    COVERAGE_PROCESS_START=pyproject.toml uv run coverage run -m snektest tests/

check-coverage:
    sh -c 'out="$(uv run coverage combine 2>&1)" || { echo "$out" >&2; echo "$out" | rg -q "No data to combine" && exit 0; exit 1; }'
    uv run coverage report

coverage-html:
    sh -c 'out="$(uv run coverage combine 2>&1)" || { echo "$out" >&2; echo "$out" | rg -q "No data to combine" && exit 0; exit 1; }'
    uv run coverage html

coverage-open:
    sh -c 'if command -v open >/dev/null 2>&1; then open htmlcov/index.html; elif command -v xdg-open >/dev/null 2>&1; then xdg-open htmlcov/index.html; else echo "No opener found (need open or xdg-open)" >&2; exit 1; fi'

coverage-report: coverage-html coverage-open

test-report: test coverage-report

test-check-coverage: test check-coverage
