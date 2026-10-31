"""Native engine selection and integration adapters through real CLI processes."""

lazy import json
lazy import subprocess
lazy import sys
lazy from pathlib import Path
lazy from textwrap import dedent
lazy from typing import Literal

lazy from snektest import (
    Param,
    assert_eq,
    assert_in,
    assert_isinstance,
    temporary_directory,
    test,
)
lazy from testutils.helpers import create_test_file

_WORKERS: list[Param[int | None]] = [
    Param(value=None, name="local"),
    Param(value=1, name="worker"),
    Param(value=2, name="workers"),
]
_MODES: list[Param[Literal["flag", "config", "override"]]] = [
    Param(value="flag", name="flag"),
    Param(value="config", name="config"),
    Param(value="override", name="override"),
]


def _cli(
    path: Path, *, workers: int | None = None, flags: tuple[str, ...] = ()
) -> dict[str, object]:
    command = [
        sys.executable,
        "-X",
        "context_aware_warnings=1",
        "-m",
        "snektest",
        "--json-output",
        *flags,
    ]
    if workers is not None:
        command.extend(("--workers", str(workers)))
    command.append(str(path))
    completed = subprocess.run(
        command,
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert_eq(completed.returncode, 0, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    decoded = assert_isinstance(json.loads(completed.stdout), dict)
    return {assert_isinstance(key, str): value for key, value in decoded.items()}


@test(_WORKERS, _MODES, mark="slow")
def test_cli_explicit_backend_and_configuration_precedence(
    workers: int | None, mode: Literal["flag", "config", "override"]
) -> None:
    directory = temporary_directory()
    if mode != "flag":
        configured = "trio" if mode == "config" else "asyncio"
        _ = (directory / "pyproject.toml").write_text(
            f'[tool.snektest]\nbackend = "{configured}"\n'
        )
    path = create_test_file(
        directory,
        dedent("""
        import asyncio
        import trio
        from snektest import assert_eq, assert_raises, test
        @test(mark="medium")
        async def test_native() -> None:
            with assert_raises(RuntimeError):
                _ = asyncio.get_running_loop()
            before = trio.lowlevel.current_task()
            await trio.sleep(0)
            assert_eq(trio.lowlevel.current_task(), before)
    """),
    )
    flags = () if mode == "config" else ("--backend", "trio")
    result = _cli(path, workers=workers, flags=flags)
    assert_eq(result["exit_code"], 0)


@test(_WORKERS, mark="slow")
def test_native_hypothesis_typing_memory_and_benchmark_adapters(
    workers: int | None,
) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        from typing import assert_type
        import trio
        from hypothesis import settings, strategies as st
        from snektest import (
            BadRequestError, assert_benchmark, assert_eq, assert_memory, assert_raises,
            assert_type_error, fixture, load_fixture, test, test_hypothesis, test_typing,
        )
        @fixture
        async def resource() -> AsyncGenerator[str]:
            task = trio.lowlevel.current_task()
            with trio.CancelScope():
                yield "native"
                assert_eq(trio.lowlevel.current_task(), task)
        @settings(max_examples=4, deadline=None)
        @test_hypothesis(st.integers(), mark="medium")
        async def test_property(value: int) -> None:
            assert_eq(await load_fixture(resource()), "native")
            await trio.sleep(0)
            assert_eq(value + 0, value)
        @test_typing
        def test_static(number: int) -> None:
            assert_type(number + 1, int)
            with assert_type_error("invalid-argument-type"):
                len(number)
        @test(mark="medium")
        async def test_measurements() -> None:
            with assert_memory(peak_below=1_000_000):
                value = bytearray(100)
                assert_eq(len(value), 100)
            with assert_raises(BadRequestError, match="must not await"):
                with assert_memory(peak_below=1_000_000):
                    await trio.sleep(0)
            with assert_benchmark(median_below=1, rounds=2, warmup=0) as timing:
                for _ in timing.rounds:
                    await trio.sleep(0)
    """),
    )
    result = _cli(path, workers=workers, flags=("--backend", "trio"))
    assert_eq(result["exit_code"], 0)


@test(_WORKERS, mark="slow")
def test_native_property_timeout_drains_thread_handoff(workers: int | None) -> None:
    directory = temporary_directory()
    path = create_test_file(
        directory,
        dedent("""
        import trio
        from hypothesis import settings, strategies as st
        from snektest import assert_eq, test, test_hypothesis
        @settings(max_examples=4, deadline=None)
        @test_hypothesis(st.integers(), mark="medium")
        async def test_property(value: int) -> None:
            await trio.sleep_forever()
        @test(mark="fast")
        def test_later() -> None:
            assert_eq(1, 1)
    """),
    )
    command = [
        sys.executable,
        "-X",
        "context_aware_warnings=1",
        "-m",
        "snektest",
        "--backend",
        "trio",
        "--json-output",
        "--timeout",
        "2" if workers is not None else "0.05",
    ]
    if workers is not None:
        command.extend(("--workers", str(workers)))
    command.append(str(path))
    completed = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, check=False, timeout=15
    )
    assert_eq(completed.returncode, 1, msg=completed.stdout + completed.stderr)
    assert_eq(completed.stderr, "")
    result = json.loads(completed.stdout)
    assert_in("TestTimeoutError", completed.stdout)
    assert_eq(result["exit_code"], 1)


@test(_WORKERS, mark="slow")
def test_native_schema_engines_keep_fixture_host_services_live(
    workers: int | None,
) -> None:
    directory = temporary_directory()
    schema = {
        "openapi": "3.0.3",
        "info": {"title": "Native", "version": "1"},
        "paths": {
            "/widgets": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                            "links": {
                                "lookup": {
                                    "operationId": "getWidget",
                                    "parameters": {"widget_id": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/widgets/{widget_id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "found",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }
    _ = (directory / "schema.json").write_text(json.dumps(schema))
    _ = (directory / "schema.graphql").write_text("type Query { ping: Boolean! }")
    path = create_test_file(
        directory,
        dedent("""
        from collections.abc import AsyncGenerator
        from pathlib import Path
        import anyio
        from anyio.abc import SocketAttribute, SocketStream
        from hypothesis import settings
        from snektest import fixture, test_graphql, test_schema, test_schema_workflow
        @fixture(scope="run")
        async def endpoint() -> AsyncGenerator[str]:
            async def respond(stream: SocketStream) -> None:
                async with stream:
                    data = b""
                    while b"\\r\\n\\r\\n" not in data:
                        data += await stream.receive(65536)
                    headers, body = data.split(b"\\r\\n\\r\\n", 1)
                    size = 0
                    for line in headers.split(b"\\r\\n")[1:]:
                        if line.lower().startswith(b"content-length:"):
                            size = int(line.split(b":", 1)[1])
                    while len(body) < size:
                        body += await stream.receive(65536)
                    request = headers.split(b"\\r\\n", 1)[0]
                    payload = b'{"data":{"ping":true}}' if b" /graphql" in request else b'{"id":"fixed"}'
                    status = b"201 Created" if request.startswith(b"POST /widgets ") else b"200 OK"
                    await stream.send(b"HTTP/1.1 " + status + b"\\r\\nContent-Type: application/json\\r\\nConnection: close\\r\\nContent-Length: " + str(len(payload)).encode() + b"\\r\\n\\r\\n" + payload)
            async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
                async with anyio.create_task_group() as group:
                    group.start_soon(listener.serve, respond)
                    try:
                        yield f"http://127.0.0.1:{listener.extra(SocketAttribute.local_port)}"
                    finally:
                        group.cancel_scope.cancel()
        @fixture
        async def graphql_endpoint() -> AsyncGenerator[str]:
            from snektest import load_fixture
            yield await load_fixture(endpoint()) + "/graphql"
        @settings(max_examples=2, deadline=None)
        @test_schema(Path(__file__).with_name("schema.json"), base_url=endpoint(), mark="slow")
        async def test_operations() -> None:
            raise RuntimeError("metadata-only")
        @settings(max_examples=2, stateful_step_count=2, deadline=None)
        @test_schema_workflow(Path(__file__).with_name("schema.json"), base_url=endpoint(), mark="slow")
        async def test_workflow() -> None:
            raise RuntimeError("metadata-only")
        @settings(max_examples=2, deadline=None)
        @test_graphql(Path(__file__).with_name("schema.graphql"), url=graphql_endpoint(), mark="slow")
        async def test_graphql_contract() -> None:
            raise RuntimeError("metadata-only")
    """),
    )
    result = _cli(path, workers=workers, flags=("--backend", "trio"))
    assert_eq(result["exit_code"], 0)
