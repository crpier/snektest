"""Meta tests for schema-driven API contract testing."""

import json
from pathlib import Path
from textwrap import dedent

from snektest import assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


def _write_schema(directory: Path, response_schema: dict[str, object]) -> Path:
    """Write the OpenAPI document served by generated contract tests."""
    schema_path = directory / "openapi.json"
    _ = schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Users", "version": "1.0.0"},
                "paths": {
                    "/users/{user_id}": {
                        "get": {
                            "parameters": [
                                {
                                    "name": "user_id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "integer"},
                                }
                            ],
                            "responses": {
                                "200": {
                                    "description": "user",
                                    "content": {
                                        "application/json": {"schema": response_schema}
                                    },
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    return schema_path


def _write_workflow_schema(directory: Path) -> Path:
    """Write producer and consumer operations joined by an OpenAPI link."""
    schema_path = directory / "workflow-openapi.json"
    _ = schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Linked users", "version": "1.0.0"},
                "paths": {
                    "/users": {
                        "post": {
                            "operationId": "createUser",
                            "responses": {
                                "201": {
                                    "description": "created",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "required": ["id"],
                                                "properties": {
                                                    "id": {"type": "string"}
                                                },
                                            }
                                        }
                                    },
                                    "links": {
                                        "GetUser": {
                                            "operationId": "getUser",
                                            "parameters": {
                                                "user_id": "$response.body#/id"
                                            },
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
                            "responses": {
                                "200": {
                                    "description": "user",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "required": ["id"],
                                                "properties": {
                                                    "id": {"type": "string"}
                                                },
                                            }
                                        }
                                    },
                                }
                            },
                        }
                    },
                },
            }
        )
    )
    return schema_path


def _write_negative_schema(
    directory: Path,
    documented_statuses: tuple[int, ...],
) -> Path:
    """Write an operation whose request body has negatable constraints."""
    schema_path = directory / "negative-openapi.json"
    _ = schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Negative users", "version": "1.0.0"},
                "paths": {
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
                                                "name": {
                                                    "type": "string",
                                                    "minLength": 3,
                                                }
                                            },
                                        }
                                    }
                                },
                            },
                            "responses": {
                                str(status): {"description": "response"}
                                for status in documented_statuses
                            },
                        }
                    }
                },
            }
        )
    )
    return schema_path


def _write_unnegatable_schema(directory: Path) -> Path:
    """Write an operation with no request inputs that can violate a constraint."""
    schema_path = directory / "unnegatable-openapi.json"
    _ = schema_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Health", "version": "1.0.0"},
                "paths": {
                    "/health": {
                        "get": {"responses": {"400": {"description": "invalid input"}}}
                    }
                },
            }
        )
    )
    return schema_path


def _server_test_source(  # noqa: PLR0913
    schema_path: Path,
    *,
    status: int,
    response_body: str,
    fixture_target: bool,
    support_code: str = "",
    decorator_arguments: str = "",
) -> str:
    """Build a generated test that serves one real local HTTP endpoint."""
    reason = {
        200: "OK",
        400: "Bad Request",
        418: "I'm a Teapot",
        422: "Unprocessable Content",
        500: "Internal Server Error",
    }.get(status, "Response")
    uses_auth_provider = "auth=" in decorator_arguments
    requires_auth = fixture_target or uses_auth_provider
    target = (
        "base_url=api_url(), headers=auth_headers()"
        if fixture_target and not uses_auth_provider
        else "base_url=api_url()"
    )
    return dedent(
        f"""
        import asyncio
        from collections.abc import AsyncGenerator, Generator

        from hypothesis import Phase, settings

        from snektest import fixture, test_schema


        async def serve_response(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            request = await reader.readuntil(b"\\r\\n\\r\\n")
            requires_auth = {requires_auth!r}
            has_auth = b"Authorization: Bearer test-token" in request
            status = {status} if not requires_auth or has_auth else 500
            reason = {reason!r} if status == {status} else "Internal Server Error"
            body = {response_body!r}.encode()
            writer.write(
                f"HTTP/1.1 {{status}} {{reason}}\\r\\n"
                f"Content-Type: application/json\\r\\n"
                f"Content-Length: {{len(body)}}\\r\\n"
                f"Connection: close\\r\\n\\r\\n".encode() + body
            )
            await writer.drain()
            writer.close()


        @fixture(scope="session")
        async def api_url() -> AsyncGenerator[str]:
            server = await asyncio.start_server(serve_response, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                yield f"http://127.0.0.1:{{port}}"


        @fixture(scope="session")
        def auth_headers() -> Generator[dict[str, str]]:
            yield {{"Authorization": "Bearer test-token"}}


        {support_code.replace("\n", "\n        ")}


        @settings(max_examples=1, phases=[Phase.generate], database=None, deadline=None)
        @test_schema(
            {str(schema_path)!r},
            {target},
            {decorator_arguments}
            mark="slow",
        )
        async def test_api_contract() -> None:
            raise RuntimeError("declarative body must not run")
        """
    )


@test(mark="slow")
async def test_schema_operation_passes_with_fixture_target() -> None:
    """Fixture-provided URL and headers drive a passing generated request."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_schema(
        tmp_dir,
        {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
    )
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=200,
            response_body='{"id": 1}',
            fixture_target=True,
        ),
        name="test_schema_pass",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(
        result["tests"][0]["name"],
        f"{test_file}::test_api_contract[GET /users/{{user_id}}]",
    )


@test(mark="slow")
async def test_schema_server_error_counts_as_failure() -> None:
    """A generated request receiving a 5xx is a failed test, not an error."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_schema(tmp_dir, {"type": "object"})
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=500,
            response_body='{"error": "boom"}',
            fixture_target=False,
        ),
        name="test_schema_server_error",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("Server error", result["tests"][0]["exception"]["message"])


@test(mark="slow")
async def test_schema_response_mismatch_counts_as_failure() -> None:
    """A response body violating its schema is a failed test, not an error."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_schema(
        tmp_dir,
        {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
    )
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=200,
            response_body='{"id": "wrong"}',
            fixture_target=False,
        ),
        name="test_schema_response_mismatch",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in(
        "Response violates schema",
        result["tests"][0]["exception"]["message"],
    )


@test(mark="slow")
async def test_schema_applies_dynamic_auth_provider() -> None:
    """A native Schemathesis auth provider modifies generated requests."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_schema(tmp_dir, {"type": "object"})
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=200,
            response_body="{}",
            fixture_target=True,
            support_code=dedent("""
                class TokenAuth:
                    def get(self, case: object, context: object) -> str:
                        return "test-token"

                    def set(
                        self,
                        case: object,
                        data: str,
                        context: object,
                    ) -> None:
                        case.headers = case.headers or {}
                        case.headers["Authorization"] = f"Bearer {data}"
            """),
            decorator_arguments="auth=TokenAuth,",
        ),
        name="test_schema_auth",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)


@test(mark="slow")
async def test_schema_custom_check_counts_as_failure() -> None:
    """A custom response invariant is classified as a Snektest failure."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_schema(tmp_dir, {"type": "object"})
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=200,
            response_body="{}",
            fixture_target=False,
            support_code=dedent("""
                def require_contract_header(
                    context: object,
                    response: object,
                    case: object,
                ) -> None:
                    raise AssertionError("missing contract header")
            """),
            decorator_arguments="checks=[require_contract_header],",
        ),
        name="test_schema_custom_check",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("missing contract header", result["tests"][0]["exception"]["message"])


@test(mark="slow")
async def test_schema_workflow_follows_openapi_link() -> None:
    """A producer response supplies the path parameter for its linked consumer."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_workflow_schema(tmp_dir)
    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            import asyncio
            from collections.abc import AsyncGenerator

            from hypothesis import Phase, settings

            from snektest import (
                SchemaFilter,
                SchemaOperationSelector,
                fixture,
                test_schema_workflow,
            )


            created_users: set[str] = set()


            async def serve_response(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                request = await reader.readuntil(b"\\r\\n\\r\\n")
                request_line = request.split(b"\\r\\n", maxsplit=1)[0]
                has_auth = b"Authorization: Bearer workflow-token" in request
                if request_line.startswith(b"POST /users ") and has_auth:
                    created_users.add("42")
                    status = 201
                    reason = "Created"
                    body = b'{{"id": "42"}}'
                elif (
                    request_line.startswith(b"GET /users/42 ")
                    and "42" in created_users
                    and has_auth
                ):
                    status = 200
                    reason = "OK"
                    body = b'{{"id": "42"}}'
                else:
                    status = 500
                    reason = "Internal Server Error"
                    body = b'{{"error": "invalid workflow"}}'
                writer.write(
                    f"HTTP/1.1 {{status}} {{reason}}\\r\\n"
                    f"Content-Type: application/json\\r\\n"
                    f"Content-Length: {{len(body)}}\\r\\n"
                    f"Connection: close\\r\\n\\r\\n".encode() + body
                )
                await writer.drain()
                writer.close()


            @fixture(scope="session")
            async def api_url() -> AsyncGenerator[str]:
                server = await asyncio.start_server(serve_response, "127.0.0.1", 0)
                port = server.sockets[0].getsockname()[1]
                async with server:
                    yield f"http://127.0.0.1:{{port}}"


            def observe_linked_consumer(
                context: object,
                response: object,
                case: object,
            ) -> None:
                if case.method == "GET":
                    raise AssertionError("linked consumer reached")


            class WorkflowAuth:
                def get(self, case: object, context: object) -> str:
                    return "workflow-token"

                def set(
                    self,
                    case: object,
                    data: str,
                    context: object,
                ) -> None:
                    case.headers = case.headers or {{}}
                    case.headers["Authorization"] = f"Bearer {{data}}"


            @settings(
                max_examples=10,
                stateful_step_count=4,
                phases=[Phase.generate],
                database=None,
                deadline=None,
            )
            @test_schema_workflow(
                {str(schema_path)!r},
                base_url=api_url(),
                auth=WorkflowAuth,
                checks=[observe_linked_consumer],
                operations=SchemaFilter(
                    include=(
                        SchemaOperationSelector(operation_id="createUser"),
                        SchemaOperationSelector(operation_id="getUser"),
                    )
                ),
                mark="slow",
            )
            async def test_user_workflow() -> None:
                raise RuntimeError("declarative body must not run")
        """),
        name="test_schema_workflow",
    )

    result = run_test_subprocess(test_file, timeout=10)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    message = result["tests"][0]["exception"]["message"]
    assert_in("linked consumer reached", message)
    assert_in("Minimized workflow:", message)
    assert_in("1. POST /users -> 201", message)
    assert_in("GET /users/42 -> 200", message)
    assert_eq(result["tests"][0]["name"], f"{test_file}::test_user_workflow")


@test(mark="slow")
async def test_schema_negative_generation_accepts_documented_4xx() -> None:
    """A documented allowed client error passes a negative operation test."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_negative_schema(tmp_dir, (422,))
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=422,
            response_body='{"error": "invalid"}',
            fixture_target=False,
            decorator_arguments='generation="negative",',
        ),
        name="test_schema_negative_rejected",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(
        result["tests"][0]["name"],
        f"{test_file}::test_api_contract[negative POST /users]",
    )


@test(mark="slow")
async def test_schema_negative_generation_rejects_2xx_acceptance() -> None:
    """Accepting schema-violating input is classified as a contract failure."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_negative_schema(tmp_dir, (200, 422))
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=200,
            response_body='{"accepted": true}',
            fixture_target=False,
            decorator_arguments='generation="negative", expected_statuses={422},',
        ),
        name="test_schema_negative_accepted",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in(
        "API accepted schema-violating request",
        result["tests"][0]["exception"]["message"],
    )


@test(mark="slow")
async def test_schema_negative_generation_enforces_expected_statuses() -> None:
    """A documented 4xx outside the configured rejection set fails."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_negative_schema(tmp_dir, (400, 422))
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=400,
            response_body='{"error": "invalid"}',
            fixture_target=False,
            decorator_arguments='generation="negative", expected_statuses={422},',
        ),
        name="test_schema_negative_wrong_4xx",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in(
        "API accepted schema-violating request",
        result["tests"][0]["exception"]["message"],
    )


@test(mark="slow")
async def test_schema_negative_generation_requires_documented_status() -> None:
    """An allowed but undocumented client error still violates the API contract."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_negative_schema(tmp_dir, (422,))
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=418,
            response_body='{"error": "invalid"}',
            fixture_target=False,
            decorator_arguments='generation="negative", expected_statuses={418},',
        ),
        name="test_schema_negative_undocumented",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in(
        "Undocumented HTTP status code",
        result["tests"][0]["exception"]["message"],
    )


@test(mark="slow")
async def test_schema_negative_generation_rejects_server_error() -> None:
    """A 5xx cannot count as valid rejection of schema-violating input."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_negative_schema(tmp_dir, (500,))
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=500,
            response_body='{"error": "boom"}',
            fixture_target=False,
            decorator_arguments='generation="negative",',
        ),
        name="test_schema_negative_server_error",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("Server error", result["tests"][0]["exception"]["message"])


@test(mark="slow")
async def test_schema_negative_generation_reports_unnegatable_operation() -> None:
    """An operation without request constraints produces an actionable error."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    schema_path = _write_unnegatable_schema(tmp_dir)
    test_file = create_test_file(
        tmp_dir,
        _server_test_source(
            schema_path,
            status=400,
            response_body='{"error": "invalid"}',
            fixture_target=False,
            decorator_arguments='generation="negative",',
        ),
        name="test_schema_negative_unnegatable",
    )

    result = run_test_subprocess(test_file, timeout=5)

    assert_eq(result["passed"], 0)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_in(
        "may have no negatable request constraints",
        result["tests"][0]["exception"]["message"],
    )
    assert_in("SchemaFilter", result["tests"][0]["exception"]["message"])
