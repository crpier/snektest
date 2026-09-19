"""Real HTTP coverage for generated GraphQL queries."""

import asyncio
from json import dumps
from pathlib import Path
from textwrap import dedent
from typing import Literal

from graphql import build_schema, introspection_from_schema

from snektest import (
    Param,
    assert_eq,
    assert_in,
    assert_not_in,
    load_fixture,
    test,
    test_graphql,
)
from snektest.utils import get_test_function_params
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


def _write_contract(  # noqa: PLR0913
    directory: Path,
    *,
    fails: bool,
    response_body: str | None = None,
    support_code: str = "",
    decorator_arguments: str = "",
    require_auth: bool = False,
    schema_format: Literal["sdl", "raw", "wrapped"] = "sdl",
    endpoint_path: str = "/api/graphql",
    response_mode: str = "normal",
) -> Path:
    """Serve actual GraphQL execution on a non-default endpoint path."""
    schema_path = directory / "schema.graphql"
    sdl = (
        "type Query { greeting(name: String!): String! }\n"
        "type Mutation { erase: Boolean! }\n"
    )
    if schema_format == "sdl":
        _ = schema_path.write_text(sdl)
    else:
        schema_path = directory / "introspection.json"
        introspection = introspection_from_schema(build_schema(sdl))
        _ = schema_path.write_text(
            dumps(
                {"data": introspection} if schema_format == "wrapped" else introspection
            )
        )
    return create_test_file(
        directory,
        dedent(
            rf"""
            import asyncio
            import json
            from collections.abc import AsyncGenerator, Generator
            from typing import Any
            from graphql import build_schema, graphql_sync
            from hypothesis import Phase, settings
            from snektest import GraphQLFilter, GraphQLOperationSelector, fixture, test_graphql

            schema = build_schema({sdl!r})

            class Root:
                def greeting(self, info: object, name: str) -> str:
                    if {fails!r}:
                        raise ValueError("resolver exploded")
                    return "Hello " + name

                def erase(self, info: object) -> bool:
                    return True

            async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                request = await reader.readuntil(b"\r\n\r\n")
                headers = request.decode().split("\r\n")
                length = next(int(line.split(":", 1)[1]) for line in headers if line.lower().startswith("content-length:"))
                body = json.loads(await reader.readexactly(length))
                if {response_mode!r} in {{"disconnect", "timeout"}}:
                    if {response_mode!r} == "timeout":
                        await reader.read()
                    writer.close()
                    await writer.wait_closed()
                    return
                if {require_auth!r} and "Authorization: Bearer graphql-test-secret" not in headers:
                    response = {{"errors": [{{"message": "authentication required"}}]}}
                elif headers[0] != {f"POST {endpoint_path} HTTP/1.1"!r}:
                    response = {{"errors": [{{"message": "wrong endpoint"}}]}}
                else:
                    response = graphql_sync(schema, body["query"], root_value=Root()).formatted
                encoded = json.dumps(response).encode()
                response_override = {response_body!r}
                if response_override is not None:
                    encoded = response_override.encode()
                writer.write(
                    b"HTTP/1.1 {"500 Internal Server Error" if response_mode == "server-error" else "200 OK"}\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {{len(encoded)}}\r\nConnection: close\r\n\r\n".encode()
                    + encoded
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            @fixture(scope="session")
            async def endpoint() -> AsyncGenerator[str]:
                server = await asyncio.start_server(respond, "127.0.0.1", 0)
                async with server:
                    yield f"http://127.0.0.1:{{server.sockets[0].getsockname()[1]}}{endpoint_path}"

            {support_code.replace(chr(10), chr(10) + "            ")}

            @settings(max_examples=3, phases=[Phase.generate, Phase.shrink], database=None, deadline=None)
            @test_graphql({str(schema_path)!r}, url=endpoint(), {decorator_arguments} mark="slow")
            async def test_contract() -> None:
                raise RuntimeError("metadata-only body must not execute")
            """
        ),
        name="test_graphql_contract",
    )


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    [
        Param[Literal["sdl", "raw", "wrapped"]](value="sdl", name="sdl"),
        Param[Literal["sdl", "raw", "wrapped"]](value="raw", name="raw"),
        Param[Literal["sdl", "raw", "wrapped"]](value="wrapped", name="wrapped"),
    ],
    mark="slow",
)
async def test_graphql_query_passes(
    worker_args: tuple[str, ...], schema_format: Literal["sdl", "raw", "wrapped"]
) -> None:
    """Generated queries use the fixture endpoint without running mutations."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract, directory, fails=False, schema_format=schema_format
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, *worker_args, timeout=15
    )

    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["tests"][0]["name"], f"{test_file}::test_contract[Query.greeting]")


@test(mark="slow")
async def test_graphql_error_is_attributed() -> None:
    """HTTP 200 resolver failures retain the root field and minimized query."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(_write_contract, directory, fails=True)

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    message = result["tests"][0]["exception"]["message"]
    assert_in("Query.greeting", message)
    assert_in("resolver exploded", message)
    assert_in('greeting(name: "")', message)


@test(
    [
        Param(
            value='{"data": {"greeting": null}, "errors": [{"message": "partial failure", "path": ["greeting"]}]}',
            name="partial-data",
        ),
        Param(value='{"errors": [{"message": "invalid query"}]}', name="request-error"),
        Param(value="[]", name="non-object"),
        Param(value="not json", name="malformed-json"),
    ],
    mark="slow",
)
async def test_graphql_bad_response_fails(response_body: str) -> None:
    """HTTP 200 does not hide GraphQL errors or malformed response documents."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract, directory, fails=False, response_body=response_body
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)


@test(mark="medium")
async def test_graphql_collects_custom_query_root() -> None:
    """Every custom query root field is selected, but writes and streams are not."""
    directory = load_fixture(tmp_dir_fixture())
    schema_path = directory / "custom.graphql"
    _ = await asyncio.to_thread(
        schema_path.write_text,
        "schema { query: Read mutation: Write subscription: Stream }\n"
        "type Read { first: String second: Int }\n"
        "type Write { erase: Boolean }\n"
        "type Stream { events: String }\n",
    )

    decorate = await asyncio.to_thread(
        test_graphql, schema_path, url="http://127.0.0.1:1/graphql"
    )

    @decorate
    async def contract() -> None:
        """Collection must not execute this body or contact the endpoint."""

    assert_eq(list(get_test_function_params(contract)), ["Read.first", "Read.second"])


@test(
    [
        Param(
            value='headers={"Authorization": "Bearer graphql-test-secret"},',
            name="literal",
        ),
        Param(value="headers=sync_headers(),", name="sync-fixture"),
        Param(value="headers=async_headers(),", name="async-fixture"),
        Param(value="auth=TokenAuth,", name="native-auth"),
    ],
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
async def test_graphql_authenticated_request(
    decorator_arguments: str, worker_args: tuple[str, ...]
) -> None:
    """Authentication reaches the HTTP server through each supported mechanism."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=False,
        require_auth=True,
        decorator_arguments=decorator_arguments,
        support_code=dedent("""
            @fixture
            def sync_headers() -> Generator[dict[str, str]]:
                yield {"Authorization": "Bearer graphql-test-secret"}

            @fixture
            async def async_headers() -> AsyncGenerator[dict[str, str]]:
                await asyncio.sleep(0)
                yield {"Authorization": "Bearer graphql-test-secret"}

            class TokenAuth:
                def get(self, case: Any, context: Any) -> str:
                    return "graphql-test-secret"

                def set(self, case: Any, data: Any, context: Any) -> None:
                    case.headers = case.headers or {}
                    case.headers["Authorization"] = f"Bearer {data}"
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, *worker_args, timeout=15
    )

    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)


@test(
    [
        Param(
            value='headers={"Authorization": "Bearer graphql-test-secret"},',
            name="headers",
        ),
        Param(value="auth=TokenAuth,", name="native-auth"),
    ],
    mark="slow",
)
async def test_graphql_custom_check_failure(authentication: str) -> None:
    """Native checks become attributed failures without reproducing auth headers."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=False,
        require_auth=True,
        decorator_arguments=f"{authentication} checks=[require_request_id],",
        support_code=dedent("""
            class TokenAuth:
                def get(self, case: Any, context: Any) -> str:
                    return "graphql-test-secret"

                def set(self, case: Any, data: Any, context: Any) -> None:
                    case.headers = case.headers or {}
                    case.headers["Authorization"] = f"Bearer {data}"

            def require_request_id(context: Any, response: Any, case: Any) -> None:
                if "X-Request-ID" not in response.headers:
                    raise AssertionError("missing request ID")
        """),
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("missing request ID", result["tests"][0]["exception"]["message"])
    assert_in("Query.greeting", result["tests"][0]["exception"]["message"])
    assert_not_in("graphql-test-secret", str(result["tests"][0]["exception"]))


@test(mark="slow")
async def test_graphql_custom_check_preserves_defaults() -> None:
    """A passing custom check cannot mask GraphQL resolver errors."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=True,
        decorator_arguments="checks=[accept_response],",
        support_code=dedent("""
            def accept_response(context: Any, response: Any, case: Any) -> None:
                pass
        """),
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("resolver exploded", result["tests"][0]["exception"]["message"])


@test(mark="slow")
async def test_graphql_custom_check_exception_is_error() -> None:
    """Programming errors in a check are not mislabeled contract failures."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=False,
        decorator_arguments="checks=[broken_check],",
        support_code=dedent("""
            def broken_check(context: Any, response: Any, case: Any) -> None:
                raise RuntimeError("broken check configuration")
        """),
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_in("broken check configuration", result["tests"][0]["exception"]["message"])


@test(
    [Param(value=(), name="local"), Param(value=("--workers", "1"), name="worker")],
    mark="slow",
)
async def test_graphql_mutation_executes(worker_args: tuple[str, ...]) -> None:
    """An opted-in mutation runs over HTTP and supports its exact CLI selector."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=False,
        decorator_arguments=(
            "allow_mutations=True, "
            'operations=GraphQLFilter(include=(GraphQLOperationSelector(kind="mutation"),)), '
        ),
    )
    selector = f"{test_file}::test_contract[Mutation.erase]"

    result = await asyncio.to_thread(
        run_test_subprocess, Path(selector), *worker_args, timeout=15
    )

    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["tests"][0]["name"], selector)


@test(
    [
        Param(value="/", name="root"),
        Param(
            value="/api%20gateway/graphql?tenant=blue&tenant=green",
            name="encoded-path-query",
        ),
    ],
    mark="slow",
)
async def test_graphql_preserves_endpoint(endpoint_path: str) -> None:
    """The complete endpoint includes escaped path segments and repeated query keys."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract, directory, fails=False, endpoint_path=endpoint_path
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["passed"], 1)
    assert_eq(result["errors"], 0)


@test(
    [
        Param(value="disconnect", name="disconnect"),
        Param(value="timeout", name="request-timeout"),
    ],
    mark="slow",
)
async def test_graphql_transport_failure_is_error(response_mode: str) -> None:
    """A disconnected or silent server is an error, not a contract violation."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract,
        directory,
        fails=False,
        response_mode=response_mode,
        decorator_arguments="request_timeout=0.1,",
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 1)
    assert_in(
        "ReadTimeout" if response_mode == "timeout" else "ConnectionError",
        str(result["tests"][0]["exception"]),
    )


@test(mark="slow")
async def test_graphql_http_server_error_fails() -> None:
    """A GraphQL data object cannot hide an HTTP 500 response."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        _write_contract, directory, fails=False, response_mode="server-error"
    )

    result = await asyncio.to_thread(run_test_subprocess, test_file, timeout=15)

    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_in("500", result["tests"][0]["exception"]["message"])
    assert_in("Query.greeting", result["tests"][0]["exception"]["message"])
