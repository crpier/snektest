# Contributor commands

# Run the same complete release-health gate used by GitHub Actions.
check:
    uv run --locked python scripts/release_check.py

check-lint:
    uv run --locked ruff check .

check-fmt:
    uv run --locked ruff format --check .

check-types:
    uv run --locked ty check

# Render console output across terminal shapes for presentation review.
# Pass snektest filters/flags through, e.g: just render tests/isolated/test_basic.py
render *ARGS:
    uv run --locked python -m testutils.render_matrix {{ARGS}}

test:
    uv run --locked snektest tests

check-coverage:
    uv run --locked coverage report

coverage-html:
    uv run --locked coverage html

coverage-open:
    sh -c 'if command -v open >/dev/null 2>&1; then open htmlcov/index.html; elif command -v xdg-open >/dev/null 2>&1; then xdg-open htmlcov/index.html; else echo "No opener found (need open or xdg-open)" >&2; exit 1; fi'

coverage-report: coverage-html coverage-open

test-report: check coverage-report

test-check-coverage: check
