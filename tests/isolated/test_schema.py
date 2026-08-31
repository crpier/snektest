"""Unit tests for the optional OpenAPI contract decorator."""

import json
import tempfile
from pathlib import Path
from types import ModuleType

from snektest import (
    Param,
    SchemaFilter,
    SchemaOperationSelector,
    assert_eq,
    assert_in,
    assert_raises,
    test,
)
from snektest.models import BadRequestError
from snektest.schema import (
    _load_optional_module,  # pyright: ignore[reportPrivateUsage]
    _resolve_runtime_value,  # pyright: ignore[reportPrivateUsage]
    test_schema,
    test_schema_workflow,
)
from snektest.utils import (
    get_test_function_markers,
    get_test_function_params,
    is_test_function,
)


def _write_schema(directory: Path, paths: dict[str, object]) -> Path:
    """Write a minimal OpenAPI schema for decorator-level tests."""
    schema_path = directory / "openapi.json"
    _ = schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Test API", "version": "1.0.0"},
                "paths": paths,
            }
        )
    )
    return schema_path


def _linked_paths(
    *,
    target_operation: str = "getUser",
    expression: str = "$response.body#/id",
) -> dict[str, object]:
    """Build two operations connected by one explicit OpenAPI link."""
    return {
        "/users": {
            "post": {
                "operationId": "createUser",
                "responses": {
                    "201": {
                        "description": "created",
                        "links": {
                            "GetUser": {
                                "operationId": target_operation,
                                "parameters": {"user_id": expression},
                            }
                        },
                    }
                },
            }
        },
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [
                    {
                        "name": "user_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "user"}},
            }
        },
    }


def _filter_paths() -> dict[str, object]:
    """Build operations with distinct filter metadata."""
    return {
        "/admin": {
            "delete": {
                "deprecated": True,
                "operationId": "deleteAdmin",
                "responses": {"204": {"description": "deleted"}},
                "tags": ["admin"],
            }
        },
        "/status": {
            "post": {
                "operationId": "updateStatus",
                "responses": {"200": {"description": "updated"}},
                "tags": ["internal"],
            }
        },
        "/users": {
            "get": {
                "operationId": "listUsers",
                "responses": {"200": {"description": "users"}},
                "tags": ["public"],
            }
        },
    }


def _negative_paths() -> dict[str, object]:
    """Build an operation with request constraints that can be violated."""
    return {
        "/users": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string", "minLength": 3}
                                },
                            }
                        }
                    },
                },
                "responses": {"422": {"description": "invalid input"}},
            }
        }
    }


@test(mark="medium")
def test_schema_marks_one_case_per_operation() -> None:
    """Each operation becomes a named Snektest parameter case."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            {
                "/users": {
                    "get": {"responses": {"200": {"description": "ok"}}},
                    "post": {"responses": {"201": {"description": "created"}}},
                }
            },
        )

        @test_schema(schema_path, base_url="http://127.0.0.1:8000", mark="slow")
        def contract() -> None:
            pass

        assert_eq(is_test_function(contract), True)
        assert_eq(get_test_function_markers(contract), ("slow",))
        assert_eq(
            set(get_test_function_params(contract)),
            {"GET /users", "POST /users"},
        )


@test(mark="medium")
def test_schema_rejects_schema_without_operations() -> None:
    """An empty schema fails collection instead of silently collecting no test."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), {})

        with assert_raises(BadRequestError) as raised:
            _ = test_schema(schema_path, base_url="http://127.0.0.1:8000")

        assert_in("contains no operations", str(raised.exception))


@test(mark="medium")
def test_schema_rejects_invalid_marker() -> None:
    """Schema tests use the same marker validation as other decorators."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
        )

        with assert_raises(TypeError):
            _ = test_schema(
                schema_path,
                base_url="http://127.0.0.1:8000",
                mark="network",  # pyright: ignore[reportArgumentType]
            )


@test(mark="fast")
def test_schema_missing_extra_has_installation_message() -> None:
    """A missing optional dependency names the extra that installs it."""

    def missing_import(module_name: str) -> ModuleType:
        raise ModuleNotFoundError(name=module_name)

    with assert_raises(BadRequestError) as raised:
        _ = _load_optional_module("schemathesis", importer=missing_import)

    assert_in("snektest[schema]", str(raised.exception))


@test(mark="fast")
def test_schema_preserves_transitive_import_errors() -> None:
    """A broken installed dependency is not mislabeled as a missing extra."""

    def broken_import(_module_name: str) -> ModuleType:
        raise ModuleNotFoundError(name="transitive_dependency")

    with assert_raises(ModuleNotFoundError) as raised:
        _ = _load_optional_module("schemathesis", importer=broken_import)

    assert_eq(raised.exception.name, "transitive_dependency")


@test(mark="fast")
async def test_schema_resolves_literal_runtime_value() -> None:
    """Literal target values pass through unchanged."""
    assert_eq(
        await _resolve_runtime_value("http://127.0.0.1:8000"), "http://127.0.0.1:8000"
    )


@test(mark="medium")
def test_schema_workflow_marks_one_unparameterized_test() -> None:
    """A linked workflow is reported as one shrinkable Snektest result."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            _linked_paths(),
        )

        @test_schema_workflow(
            schema_path,
            base_url="http://127.0.0.1:8000",
            mark="slow",
        )
        def workflow() -> None:
            pass

        assert_eq(is_test_function(workflow), True)
        assert_eq(get_test_function_markers(workflow), ("slow",))
        assert_eq(get_test_function_params(workflow), {"": ()})


@test(mark="medium")
def test_schema_workflow_rejects_schema_without_links() -> None:
    """A linkless schema fails collection with setup guidance."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
        )

        with assert_raises(BadRequestError) as raised:
            _ = test_schema_workflow(
                schema_path,
                base_url="http://127.0.0.1:8000",
            )

        assert_in(str(schema_path), str(raised.exception))
        assert_in("contains no usable workflow links", str(raised.exception))
        assert_in("Define OpenAPI links", str(raised.exception))


@test(mark="medium")
def test_schema_workflow_reports_unreachable_link_target() -> None:
    """An unknown target operation identifies the complete invalid transition."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            _linked_paths(target_operation="missingUser"),
        )

        with assert_raises(BadRequestError) as raised:
            _ = test_schema_workflow(
                schema_path,
                base_url="http://127.0.0.1:8000",
            )

        message = str(raised.exception)
        assert_in(str(schema_path), message)
        assert_in("POST /users -> [201] GetUser -> missingUser", message)
        assert_in("Operation 'missingUser' not found", message)


@test(mark="medium")
def test_schema_workflow_reports_invalid_link_expression() -> None:
    """A malformed runtime expression identifies its link and parser error."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(
            Path(tmp),
            _linked_paths(expression="$unknown"),
        )

        with assert_raises(BadRequestError) as raised:
            _ = test_schema_workflow(
                schema_path,
                base_url="http://127.0.0.1:8000",
            )

        message = str(raised.exception)
        assert_in("POST /users -> [201] GetUser -> GET /users/{user_id}", message)
        assert_in("Invalid expression `$unknown`", message)


@test(mark="fast")
def test_schema_operation_selector_requires_criterion() -> None:
    """An empty selector is rejected instead of silently matching everything."""
    with assert_raises(BadRequestError) as raised:
        _ = SchemaOperationSelector()

    assert_in("requires at least one matching criterion", str(raised.exception))


@test(
    [
        Param(
            value=(SchemaOperationSelector(path="/users"), "GET /users"),
            name="path",
        ),
        Param(
            value=(SchemaOperationSelector(method="delete"), "DELETE /admin"),
            name="method",
        ),
        Param(
            value=(SchemaOperationSelector(tag="internal"), "POST /status"),
            name="tag",
        ),
        Param(
            value=(
                SchemaOperationSelector(operation_id="listUsers"),
                "GET /users",
            ),
            name="operation-id",
        ),
    ],
    mark="medium",
)
def test_schema_filter_includes_matching_criterion(
    case: tuple[SchemaOperationSelector, str],
) -> None:
    """Each supported selector criterion limits operation collection."""
    selector, expected_operation = case
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _filter_paths())

        @test_schema(
            schema_path,
            base_url="http://127.0.0.1:8000",
            operations=SchemaFilter(include=(selector,)),
        )
        def contract() -> None:
            pass

        assert_eq(set(get_test_function_params(contract)), {expected_operation})


@test(mark="medium")
def test_schema_filter_combines_include_selectors_as_alternatives() -> None:
    """Multiple include selectors retain operations matching either selector."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _filter_paths())

        @test_schema(
            schema_path,
            base_url="http://127.0.0.1:8000",
            operations=SchemaFilter(
                include=(
                    SchemaOperationSelector(path="/users"),
                    SchemaOperationSelector(tag="internal"),
                )
            ),
        )
        def contract() -> None:
            pass

        assert_eq(
            set(get_test_function_params(contract)),
            {"GET /users", "POST /status"},
        )


@test(mark="medium")
def test_schema_filter_excludes_matching_operations() -> None:
    """Exclude selectors take precedence over the default include-all behavior."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _filter_paths())

        @test_schema(
            schema_path,
            base_url="http://127.0.0.1:8000",
            operations=SchemaFilter(
                exclude=(SchemaOperationSelector(method="DELETE"),)
            ),
        )
        def contract() -> None:
            pass

        assert_eq(
            set(get_test_function_params(contract)),
            {"GET /users", "POST /status"},
        )


@test(mark="medium")
def test_schema_filter_excludes_deprecated_operations() -> None:
    """The deprecated switch removes operations marked by the OpenAPI document."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _filter_paths())

        @test_schema(
            schema_path,
            base_url="http://127.0.0.1:8000",
            operations=SchemaFilter(exclude_deprecated=True),
        )
        def contract() -> None:
            pass

        assert_eq(
            set(get_test_function_params(contract)),
            {"GET /users", "POST /status"},
        )


@test(mark="medium")
def test_schema_filter_rejects_empty_selection() -> None:
    """A filter selecting no operations fails collection with its schema path."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _filter_paths())

        with assert_raises(BadRequestError) as raised:
            _ = test_schema(
                schema_path,
                base_url="http://127.0.0.1:8000",
                operations=SchemaFilter(
                    include=(SchemaOperationSelector(path="/missing"),)
                ),
            )

        assert_in(str(schema_path), str(raised.exception))
        assert_in("selected no operations", str(raised.exception))


@test(mark="medium")
def test_schema_workflow_filter_keeps_selected_link() -> None:
    """A workflow remains collectable when both linked operations are selected."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _linked_paths())

        @test_schema_workflow(
            schema_path,
            base_url="http://127.0.0.1:8000",
            operations=SchemaFilter(
                include=(
                    SchemaOperationSelector(operation_id="createUser"),
                    SchemaOperationSelector(operation_id="getUser"),
                )
            ),
        )
        def workflow() -> None:
            pass

        assert_eq(is_test_function(workflow), True)


@test(mark="medium")
def test_schema_workflow_filter_rejects_partial_link() -> None:
    """Filtering out one end of every link reports how to retain a workflow."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _linked_paths())

        with assert_raises(BadRequestError) as raised:
            _ = test_schema_workflow(
                schema_path,
                base_url="http://127.0.0.1:8000",
                operations=SchemaFilter(
                    include=(SchemaOperationSelector(operation_id="createUser"),)
                ),
            )

        assert_in("left no usable workflow links", str(raised.exception))
        assert_in("both the producer and consumer", str(raised.exception))


@test(mark="medium")
def test_schema_negative_generation_names_cases_distinctly() -> None:
    """Negative operation cases expose their generation mode in test filters."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _negative_paths())

        @test_schema(
            schema_path,
            base_url="http://127.0.0.1:8000",
            generation="negative",
        )
        def invalid_contract() -> None:
            pass

        assert_eq(
            set(get_test_function_params(invalid_contract)),
            {"negative POST /users"},
        )


@test(mark="medium")
def test_schema_negative_generation_requires_expected_status() -> None:
    """An empty rejection-status set fails collection clearly."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _negative_paths())

        with assert_raises(BadRequestError) as raised:
            _ = test_schema(
                schema_path,
                base_url="http://127.0.0.1:8000",
                generation="negative",
                expected_statuses=(),
            )

        assert_in("requires at least one expected status", str(raised.exception))


@test(mark="medium")
def test_schema_negative_generation_rejects_non_4xx_status() -> None:
    """Successful and server-error statuses cannot count as input rejection."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = _write_schema(Path(tmp), _negative_paths())

        with assert_raises(BadRequestError) as raised:
            _ = test_schema(
                schema_path,
                base_url="http://127.0.0.1:8000",
                generation="negative",
                expected_statuses=(200, 500),
            )

        assert_in("expected statuses must all be 4xx", str(raised.exception))
