"""GraphQL contracts; install snektest[schema] first.

Save your service's SDL as schema.graphql and run against a disposable service.
Alternatively, replace the schema paths below with introspection.json containing
a raw __schema object or a successful data-wrapped introspection response.
The mutation test below can repeat writes during shrinking.
HTTP server-error diagnostics include the status code and generated operation.
Subscriptions and complete response-type validation are unsupported.
"""

from collections.abc import Generator
from typing import Any

from hypothesis import settings

from snektest import (
    GraphQLFilter,
    GraphQLOperationSelector,
    assert_in,
    fixture,
    test_graphql,
)


@fixture
def graphql_headers() -> Generator[dict[str, str]]:
    """Replace this token with credentials for the disposable service."""
    yield {"Authorization": "Bearer test-token"}


def require_request_id(_context: Any, response: Any, _case: Any) -> None:
    """Require a response header in addition to the GraphQL error checks."""
    assert_in("x-request-id", response.headers)


@settings(max_examples=50, deadline=None)
@test_graphql(
    "schema.graphql",
    url="http://127.0.0.1:8000/graphql",
    headers=graphql_headers(),
    checks=[require_request_id],
    request_timeout=5.0,
    mark="slow",
)
async def test_graphql_contract() -> None:
    """Generate positive queries for every query root field."""


@settings(max_examples=10, deadline=None)
@test_graphql(
    "schema.graphql",
    url="http://127.0.0.1:8000/graphql",
    headers=graphql_headers(),
    allow_mutations=True,
    operations=GraphQLFilter(
        include=(GraphQLOperationSelector(kind="mutation", field="createWidget"),),
    ),
    mark="slow",
)
async def test_create_widget_contract() -> None:
    """Exercise one mutation on a disposable service; replace the field as needed."""
