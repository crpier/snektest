"""OpenAPI operation and linked-workflow contract testing."""

from typing import Any

from hypothesis import settings

from snektest import (
    SchemaFilter,
    SchemaOperationSelector,
    test_schema,
    test_schema_workflow,
)

PUBLIC_OPERATIONS = SchemaFilter(
    exclude=(SchemaOperationSelector(tag="internal"),),
    exclude_deprecated=True,
)


def require_request_id(_context: Any, response: Any, _case: Any) -> None:
    """Require every response to carry an application request identifier."""
    if "x-request-id" not in response.headers:
        raise AssertionError("response is missing X-Request-ID")  # noqa: EM101, TRY003


@settings(max_examples=50, deadline=None)
@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    checks=[require_request_id],
    operations=PUBLIC_OPERATIONS,
    request_timeout=5.0,
    mark="slow",
)
async def test_api_contract() -> None:
    """Generate positive requests and validate every declared operation."""


@settings(max_examples=50, deadline=None)
@test_schema(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    checks=[require_request_id],
    generation="negative",
    expected_statuses={400, 422},
    operations=PUBLIC_OPERATIONS,
    request_timeout=5.0,
    mark="slow",
)
async def test_invalid_requests() -> None:
    """Generate schema-violating requests and require documented rejection."""


@settings(max_examples=50, stateful_step_count=8, deadline=None)
@test_schema_workflow(
    "openapi.json",
    base_url="http://127.0.0.1:8000",
    checks=[require_request_id],
    operations=PUBLIC_OPERATIONS,
    request_timeout=5.0,
    mark="slow",
)
async def test_api_workflows() -> None:
    """Generate linked operation sequences and shrink failing workflows."""
