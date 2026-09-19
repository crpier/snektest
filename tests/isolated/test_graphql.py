"""GraphQL collection filters and mutation safety without network access."""

import asyncio
from json import dumps
from pathlib import Path

from graphql import build_schema, introspection_from_schema

from snektest import (
    BadRequestError,
    GraphQLFilter,
    GraphQLOperationSelector,
    Param,
    assert_eq,
    assert_in,
    assert_not_in,
    assert_raises,
    load_fixture,
    test,
    test_graphql,
)
from snektest.utils import get_test_function_params
from testutils.fixtures import tmp_dir_fixture


async def _write_schema(directory: Path) -> Path:
    schema_path = directory / "schema.graphql"
    _ = await asyncio.to_thread(
        schema_path.write_text,
        "schema { query: Read mutation: Write subscription: Stream }\n"
        "type Read { shared: String health: String }\n"
        "type Write { shared: String erase: Boolean }\n"
        "type Stream { shared: String }\n",
    )
    return schema_path


@test(
    [
        Param(value=(None, False, ["Read.shared", "Read.health"]), name="default"),
        Param(
            value=(
                None,
                True,
                ["Read.shared", "Read.health", "Write.shared", "Write.erase"],
            ),
            name="mutation-opt-in",
        ),
        Param(
            value=(GraphQLFilter(), False, ["Read.shared", "Read.health"]),
            name="empty-filter",
        ),
        Param(
            value=(
                GraphQLFilter(include=(GraphQLOperationSelector(field="shared"),)),
                True,
                ["Read.shared", "Write.shared"],
            ),
            name="field-across-kinds",
        ),
        Param(
            value=(
                GraphQLFilter(
                    include=(GraphQLOperationSelector(field="shared", kind="mutation"),)
                ),
                True,
                ["Write.shared"],
            ),
            name="kind-field-and",
        ),
        Param(
            value=(
                GraphQLFilter(include=(GraphQLOperationSelector(kind="mutation"),)),
                True,
                ["Write.shared", "Write.erase"],
            ),
            name="mutation-only",
        ),
        Param(
            value=(
                GraphQLFilter(
                    include=(
                        GraphQLOperationSelector(field="erase"),
                        GraphQLOperationSelector(field="health"),
                    )
                ),
                True,
                ["Read.health", "Write.erase"],
            ),
            name="include-or-schema-order",
        ),
        Param(
            value=(
                GraphQLFilter(
                    include=(
                        GraphQLOperationSelector(field="shared"),
                        GraphQLOperationSelector(kind="query"),
                    ),
                    exclude=(
                        GraphQLOperationSelector(kind="mutation"),
                        GraphQLOperationSelector(field="health"),
                    ),
                ),
                True,
                ["Read.shared"],
            ),
            name="excludes-win",
        ),
        Param(
            value=(
                GraphQLFilter(include=(GraphQLOperationSelector(field="shared"),)),
                False,
                ["Read.shared"],
            ),
            name="filter-does-not-enable-mutations",
        ),
    ],
    mark="medium",
)
async def test_graphql_selection(
    selection: tuple[GraphQLFilter | None, bool, list[str]],
) -> None:
    """Filters use semantic kinds even when root type names are customized."""
    directory = load_fixture(tmp_dir_fixture())
    schema_path = await _write_schema(directory)
    operation_filter, allow_mutations, expected_names = selection

    decorate = await asyncio.to_thread(
        test_graphql,
        schema_path,
        url="http://127.0.0.1:1/graphql",
        operations=operation_filter,
        allow_mutations=allow_mutations,
    )

    @decorate
    async def contract() -> None:
        """Collect only; no endpoint needs to exist."""

    assert_eq(list(get_test_function_params(contract)), expected_names)


@test(
    [
        Param(
            value=GraphQLFilter(include=(GraphQLOperationSelector(kind="mutation"),)),
            name="mutation-needs-opt-in",
        ),
        Param(
            value=GraphQLFilter(include=(GraphQLOperationSelector(field="missing"),)),
            name="unknown-field",
        ),
        Param(
            value=GraphQLFilter(include=(GraphQLOperationSelector(field="Health"),)),
            name="case-sensitive",
        ),
        Param(
            value=GraphQLFilter(exclude=(GraphQLOperationSelector(kind="query"),)),
            name="exclude-all",
        ),
    ],
    mark="medium",
)
async def test_graphql_empty_selection_rejected(
    operation_filter: GraphQLFilter,
) -> None:
    """A filter that selects nothing fails before a test body can execute."""
    directory = load_fixture(tmp_dir_fixture())
    schema_path = await _write_schema(directory)

    with assert_raises(BadRequestError) as raised:
        _ = await asyncio.to_thread(
            test_graphql,
            schema_path,
            url="http://127.0.0.1:1/graphql",
            operations=operation_filter,
        )

    assert_in("selected no root fields", str(raised.exception))
    assert_in("allow_mutations", str(raised.exception))


@test(mark="fast")
def test_graphql_selector_requires_criterion() -> None:
    """An empty selector is probably a configuration mistake."""
    with assert_raises(BadRequestError):
        _ = GraphQLOperationSelector()


@test(
    [
        Param(value=field, name=str(index))
        for index, field in enumerate(
            ("", " user", "Query.user", "user*", "1user", "usér")
        )
    ],
    mark="fast",
)
def test_graphql_selector_rejects_invalid_field(field: str) -> None:
    """Fields are exact GraphQL names, not labels, patterns, or dotted paths."""
    with assert_raises(BadRequestError):
        _ = GraphQLOperationSelector(field=field)


@test(mark="fast")
def test_graphql_selector_rejects_subscription() -> None:
    """Unsupported operation kinds fail instead of disappearing from collection."""
    with assert_raises(BadRequestError):
        _ = GraphQLOperationSelector(kind="subscription")  # ty: ignore[invalid-argument-type]


@test(mark="fast")
def test_graphql_mutation_opt_in_rejects_truthy_strings() -> None:
    """A string configuration value cannot accidentally enable destructive requests."""
    with assert_raises(BadRequestError) as raised:
        _ = test_graphql(
            "unused.graphql",
            url="http://127.0.0.1:1/graphql",
            allow_mutations="false",  # ty: ignore[invalid-argument-type]
        )

    assert_in("allow_mutations must be a bool", str(raised.exception))


@test(
    [Param(value=False, name="raw"), Param(value=True, name="wrapped")],
    [
        Param(value=".json", name="json"),
        Param(value=".graphql", name="graphql"),
        Param(value="", name="no-suffix"),
    ],
    mark="medium",
)
async def test_graphql_introspection_collection(wrapped: bool, suffix: str) -> None:  # noqa: FBT001
    """Saved introspection preserves custom roots, filters, and mutation opt-in."""
    directory = load_fixture(tmp_dir_fixture())
    sdl_path = await _write_schema(directory)
    sdl = await asyncio.to_thread(sdl_path.read_text)
    introspection = introspection_from_schema(build_schema(sdl))
    document = (
        {"data": introspection, "extensions": {"exporter": "test"}}
        if wrapped
        else introspection
    )
    schema_path = directory / f"introspection{suffix}"
    _ = await asyncio.to_thread(
        schema_path.write_text, "\ufeff" + dumps(document), encoding="utf-8"
    )

    decorate = await asyncio.to_thread(
        test_graphql,
        schema_path,
        url="http://127.0.0.1:1/graphql",
        allow_mutations=True,
        operations=GraphQLFilter(include=(GraphQLOperationSelector(field="shared"),)),
    )

    @decorate
    async def contract() -> None:
        """No network access during collection."""

    assert_eq(list(get_test_function_params(contract)), ["Read.shared", "Write.shared"])


@test(
    [
        Param(value="{broken", name="invalid-json"),
        Param(value="[]", name="array"),
        Param(value="null", name="null"),
        Param(value='{"data": null}', name="null-data"),
        Param(value='{"data": {}}', name="missing-schema"),
        Param(value='{"__schema": []}', name="invalid-schema"),
        Param(
            value='{"errors": [{"message": "export-secret"}]}', name="error-response"
        ),
        Param(
            value='{"data": {"__schema": {}}, "errors": [{"message": "export-secret"}]}',
            name="partial-data",
        ),
    ],
    mark="medium",
)
async def test_graphql_invalid_introspection_rejected(document: str) -> None:
    """Malformed exports fail locally without echoing potentially sensitive content."""
    directory = load_fixture(tmp_dir_fixture())
    schema_path = directory / "introspection.json"
    _ = await asyncio.to_thread(schema_path.write_text, document)

    with assert_raises(BadRequestError) as raised:
        _ = await asyncio.to_thread(
            test_graphql, schema_path, url="http://127.0.0.1:1/graphql"
        )

    assert_in(str(schema_path), str(raised.exception))
    assert_not_in("export-secret", str(raised.exception))


@test(mark="medium")
async def test_graphql_introspection_errors_reject_valid_data() -> None:
    """A usable schema must not hide errors in its saved introspection response."""
    directory = load_fixture(tmp_dir_fixture())
    schema_path = directory / "partial-introspection.json"
    introspection = introspection_from_schema(
        build_schema("type Query { hello: String }")
    )
    _ = await asyncio.to_thread(
        schema_path.write_text,
        dumps({"data": introspection, "errors": [{"message": "export-secret"}]}),
    )

    with assert_raises(BadRequestError) as raised:
        _ = await asyncio.to_thread(
            test_graphql, schema_path, url="http://127.0.0.1:1/graphql"
        )

    assert_in("introspection errors", str(raised.exception))
    assert_not_in("export-secret", str(raised.exception))
