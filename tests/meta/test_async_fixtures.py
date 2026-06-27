"""Meta tests for async fixtures and parameterization."""

from textwrap import dedent

from snektest import load_fixture, test
from snektest.assertions import assert_eq
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
def test_async_session_fixture() -> None:
    """Test async session fixture works and is reused across tests."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import AsyncGenerator

            from snektest import fixture, load_fixture, test, assert_eq

            @fixture(scope="session")
            async def fixture_for_session() -> AsyncGenerator[int]:
                print("session fixture started")
                yield 10
                print("session fixture ended")

            @test()
            async def test_with_session_fixture() -> None:
                session_fixture_result = await load_fixture(fixture_for_session())
                assert_eq(session_fixture_result, 10)

            @test()
            async def another_test_with_session_fixture() -> None:
                session_fixture_result = await load_fixture(fixture_for_session())
                assert_eq(session_fixture_result, 10)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 2)
    assert_eq(result["failed"], 0)


@test()
def test_async_session_fixture_rejects_parameters() -> None:
    """Async session fixtures must be zero-argument."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import AsyncGenerator

            from snektest import fixture, load_fixture, test

            @fixture(scope="session")
            async def fixture_for_session(value: int) -> AsyncGenerator[int]:
                yield value

            @test()
            async def test_with_session_fixture() -> None:
                _ = await load_fixture(fixture_for_session(10))
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 0)
    assert_eq(result["errors"], 1)
    assert_eq(
        "Session fixture fixture_for_session cannot accept parameters"
        in result["tests"][0]["exception"]["message"],
        True,
    )


@test()
def test_async_session_fixture_reused_by_three_tests() -> None:
    """Async session fixture setup is cached without reusing coroutine objects."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    events_file = tmp_dir / "events.txt"

    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import AsyncGenerator
            from pathlib import Path

            from snektest import assert_eq, fixture, load_fixture, test

            EVENTS_FILE = Path({str(events_file)!r})

            def record(event: str) -> None:
                existing = EVENTS_FILE.read_text() if EVENTS_FILE.exists() else ""
                EVENTS_FILE.write_text(existing + event + "\\n")

            @fixture(scope="session")
            async def fixture_for_session() -> AsyncGenerator[int]:
                record("setup")
                yield 10
                record("teardown")

            @test()
            async def first() -> None:
                session_fixture_result = await load_fixture(fixture_for_session())
                assert_eq(session_fixture_result, 10)

            @test()
            async def second() -> None:
                session_fixture_result = await load_fixture(fixture_for_session())
                assert_eq(session_fixture_result, 10)

            @test()
            async def third() -> None:
                session_fixture_result = await load_fixture(fixture_for_session())
                assert_eq(session_fixture_result, 10)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 3)
    assert_eq(result["failed"], 0)
    assert_eq(result["errors"], 0)
    assert_eq(result["session_teardown_failed"], 0)
    assert_eq(result["stderr"], "")
    assert_eq(events_file.read_text().splitlines(), ["setup", "teardown"])


@test()
def test_async_function_fixture() -> None:
    """Test async function fixture works."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import AsyncGenerator
            from snektest import fixture, load_fixture, test, assert_eq

            @fixture
            async def simple_fixture() -> AsyncGenerator[str]:
                print("simple async fixture started")
                yield "some fixture"
                print("simple async fixture ended")

            @test()
            async def test_with_simple_fixture() -> None:
                fixture = await load_fixture(simple_fixture())
                assert_eq(fixture, "some fixture")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)


@test()
def test_parameterized_async_test() -> None:
    """Test parameterized async tests work."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_eq
            from snektest.models import Param

            first_param_set = [
                Param(" bab ", "spaces both sides"),
                Param(" bab", "space left side"),
                Param("bab ", "space right side"),
                Param("bab", "no spaces"),
            ]

            @test(first_param_set)
            async def test_1_params(param1: str) -> None:
                assert_eq(param1.strip(), "bab")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 4)
    assert_eq(result["failed"], 0)


@test()
def test_multi_param_async_test() -> None:
    """Test async tests with multiple parameter sets."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from snektest import test, assert_eq, assert_in
            from snektest.models import Param

            first_param_set = [
                Param(" bab ", "spaces both sides"),
                Param("bab", "no spaces"),
            ]
            second_param_set = [
                Param(5, "attempt 1"),
                Param(10, "attempt 2"),
            ]

            @test(first_param_set, second_param_set)
            async def test_2_params(param1: str, param2: int) -> None:
                assert_eq(param1.strip(), "bab")
                assert_in(param2, [5, 10])
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 4)
    assert_eq(result["failed"], 0)


@test()
def test_async_fixture_with_params() -> None:
    """Test async fixture that accepts parameters."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import AsyncGenerator
            from snektest import fixture, load_fixture, test, assert_eq
            from snektest.models import Param

            @fixture
            async def fixture_with_param(param1: str) -> AsyncGenerator[str]:
                yield param1

            @test([Param("value1", "param1"), Param("value2", "param2")])
            async def test_with_param_fixture(param1: str) -> None:
                result = await load_fixture(fixture_with_param(param1))
                assert_eq(result, param1)
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 2)
    assert_eq(result["failed"], 0)


@test()
def test_async_fixture_teardown() -> None:
    """Test async fixture teardown is executed."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import AsyncGenerator
            from snektest import fixture, load_fixture, test, assert_eq
            from snektest.models import Param

            @fixture
            async def fixture_with_teardown_and_param(param: str) -> AsyncGenerator[str]:
                print(f"setup: {param}")
                yield param
                print(f"teardown: {param}")

            @test([Param("the number", "single-param")])
            async def test_with_param_and_teardown_fixture(param1: str) -> None:
                fixture_result = await load_fixture(fixture_with_teardown_and_param(param1))
                assert_eq(param1, "the number")
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
