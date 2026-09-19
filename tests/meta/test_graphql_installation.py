"""GraphQL optional-extra behavior in independently installed release archives."""

import asyncio
import os
import subprocess
import sys
from json import loads
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent

from snektest import Param, assert_eq, load_fixture, test
from testutils.fixtures import tmp_dir_fixture


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("COVERAGE_PROCESS_START", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@test(
    [Param(value="wheel", name="wheel"), Param(value="sdist", name="sdist")],
    [Param(value="", name="base"), Param(value="[schema]", name="schema-extra")],
    mark="slow",
)
async def test_graphql_installed_extra_boundary(archive_kind: str, extra: str) -> None:
    """Only the schema extra enables GraphQL; both archives work outside the checkout."""
    root = load_fixture(tmp_dir_fixture())
    directory = Path(await asyncio.to_thread(lambda: mkdtemp(dir=root)))
    distribution = directory / "dist"
    built = await asyncio.to_thread(
        _run,
        ["uv", "build", f"--{archive_kind}", "--out-dir", str(distribution)],
        cwd=Path.cwd(),
    )
    assert_eq(built.returncode, 0, msg=built.stdout + built.stderr)
    archives = await asyncio.to_thread(
        lambda: list(
            distribution.glob("*.whl" if archive_kind == "wheel" else "*.tar.gz")
        )
    )
    assert_eq(len(archives), 1)
    environment = directory / "venv"
    created = await asyncio.to_thread(
        _run,
        ["uv", "venv", "--python", sys.executable, str(environment)],
        cwd=directory,
    )
    assert_eq(created.returncode, 0, msg=created.stderr)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = await asyncio.to_thread(
        _run,
        ["uv", "pip", "install", "--python", str(python), f"{archives[0]}{extra}"],
        cwd=directory,
    )
    assert_eq(installed.returncode, 0, msg=installed.stdout + installed.stderr)

    if not extra:
        consumer = dedent("""
            from importlib.util import find_spec
            from snektest import BadRequestError, assert_eq, assert_in, assert_raises, test_graphql

            assert_eq(find_spec("schemathesis"), None)
            assert_eq(find_spec("graphql"), None)
            with assert_raises(BadRequestError) as raised:
                test_graphql("unused.graphql", url="http://127.0.0.1:1/graphql")
            assert_in("snektest[schema]", str(raised.exception))
        """)
        result = await asyncio.to_thread(
            _run, [str(python), "-I", "-c", consumer], cwd=directory
        )
        assert_eq(result.returncode, 0, msg=result.stdout + result.stderr)
        return

    schema = directory / "schema.graphql"
    _ = await asyncio.to_thread(schema.write_text, "type Query { hello: String! }")
    test_file = directory / "test_contract.py"
    _ = await asyncio.to_thread(
        test_file.write_text,
        dedent(f"""
            import asyncio
            from collections.abc import AsyncGenerator
            from hypothesis import settings
            from snektest import fixture, test_graphql

            async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                request = await reader.readuntil(b"\\r\\n\\r\\n")
                length = next(int(line.split(b":", 1)[1]) for line in request.split(b"\\r\\n") if line.lower().startswith(b"content-length:"))
                await reader.readexactly(length)
                body = b'{{"data": {{"hello": "ok"}}}}'
                writer.write(b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nConnection: close\\r\\n" + f"Content-Length: {{len(body)}}\\r\\n\\r\\n".encode() + body)
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            @fixture
            async def endpoint() -> AsyncGenerator[str]:
                server = await asyncio.start_server(respond, "127.0.0.1", 0)
                async with server:
                    yield f"http://127.0.0.1:{{server.sockets[0].getsockname()[1]}}/graphql"

            @settings(max_examples=1, deadline=None, database=None)
            @test_graphql({str(schema)!r}, url=endpoint(), mark="slow")
            async def test_contract() -> None:
                raise RuntimeError("body must not execute")
        """),
    )

    result = await asyncio.to_thread(
        _run,
        [str(python), "-I", "-m", "snektest", str(test_file), "--json-output"],
        cwd=directory,
    )

    assert_eq(result.returncode, 0, msg=result.stdout + result.stderr)
    assert_eq(loads(result.stdout)["passed"], 1)
