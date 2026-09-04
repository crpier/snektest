"""Meta tests for fixture error handling."""

import asyncio
import subprocess
import sys
from textwrap import dedent

from snektest import assert_in, assert_raises, load_fixture, test
from snektest.assertions import assert_eq
from snektest.cli import run_tests_programmatic
from snektest.models import FilterItem
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test()
def test_function_fixture_teardown_failure() -> None:
    """Test that function fixture teardown failures are reported."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            from snektest import fixture, load_fixture, test

            @fixture
            def fixture_with_failing_teardown() -> Generator[None]:
                yield None
                msg = "failing teardown"
                raise ValueError(msg)

            @test()
            def test_with_bad_fixture() -> None:
                _ = load_fixture(fixture_with_failing_teardown())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["fixture_teardown_failed"], 1)


@test()
def test_malformed_function_teardown_does_not_skip_remaining_fixture() -> None:
    """Every established fixture gets one teardown attempt after malformed yield."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    events_file = tmp_dir / "teardown-events"

    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import Generator
            from pathlib import Path

            from snektest import fixture, load_fixture, test

            events_file = Path({str(events_file)!r})

            @fixture
            def valid_fixture() -> Generator[None]:
                yield None
                with events_file.open("a") as output:
                    _ = output.write("valid\\n")

            @fixture
            def malformed_fixture() -> Generator[None]:
                yield None
                with events_file.open("a") as output:
                    _ = output.write("malformed\\n")
                yield None

            @test()
            def test_uses_fixtures() -> None:
                _ = load_fixture(valid_fixture())
                _ = load_fixture(malformed_fixture())
        """),
        name="test_malformed_fixture_teardown",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["passed"], 1)
    assert_eq(result["fixture_teardown_failed"], 1)
    assert_eq(
        result["tests"][0]["fixture_teardown_failures"][0]["fixture_name"],
        "malformed_fixture",
    )
    assert_eq(events_file.read_text().splitlines(), ["malformed", "valid"])
    assert_eq(result["returncode"], 1)


@test()
def test_json_retains_output_warnings_and_all_function_teardown_failures() -> None:
    """Structured results retain every function teardown diagnostic."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import warnings
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture
            def first_fixture() -> Generator[None]:
                yield None
                print("first teardown output")
                warnings.warn("first teardown warning", stacklevel=1)
                raise ValueError("first teardown failure")

            @fixture
            def second_fixture() -> Generator[None]:
                yield None
                print("second teardown output")
                warnings.warn("second teardown warning", stacklevel=1)
                raise RuntimeError("second teardown failure")

            @test()
            def test_uses_both() -> None:
                _ = load_fixture(first_fixture())
                _ = load_fixture(second_fixture())
        """),
        name="test_multiple_teardown_diagnostics",
    )

    result = run_test_subprocess(test_file)
    test_result = result["tests"][0]

    assert_eq(result["fixture_teardown_failed"], 2)
    assert_eq(
        [
            failure["fixture_name"]
            for failure in test_result["fixture_teardown_failures"]
        ],
        ["second_fixture", "first_fixture"],
    )
    assert_eq(
        test_result["fixture_teardown_output"],
        "second teardown output\nfirst teardown output\n",
    )
    assert_in("second teardown warning", "\n".join(test_result["warnings"]))
    assert_in("first teardown warning", "\n".join(test_result["warnings"]))
    assert_eq(result["returncode"], 1)


@test()
def test_system_exit_tears_down_function_fixture_before_propagating() -> None:
    """`SystemExit` propagates only after function fixture cleanup."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    events_file = tmp_dir / "system-exit-events"

    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            from collections.abc import Generator
            from pathlib import Path

            from snektest import fixture, load_fixture, test

            @fixture
            def resource() -> Generator[None]:
                yield None
                _ = Path({str(events_file)!r}).write_text("torn down")

            @test()
            def test_exits() -> None:
                _ = load_fixture(resource())
                raise SystemExit(7)
        """),
        name="test_system_exit_cleanup",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "snektest.cli", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert_eq(completed.returncode, 7)
    assert_eq(events_file.read_text(), "torn down")


@test()
async def test_parent_cancellation_propagates_after_fixture_teardown() -> None:
    """An embedded run cleans established fixtures before cancellation escapes."""
    tmp_dir = load_fixture(tmp_dir_fixture())
    started_file = tmp_dir / "cancel-test-started"
    events_file = tmp_dir / "cancel-teardown-events"

    test_file = create_test_file(
        tmp_dir,
        dedent(f"""
            import asyncio
            from collections.abc import Generator
            from pathlib import Path

            from snektest import fixture, load_fixture, test

            events_file = Path({str(events_file)!r})

            @fixture(scope="session")
            def session_resource() -> Generator[None]:
                yield None
                with events_file.open("a") as output:
                    _ = output.write("session\\n")

            @fixture
            def function_resource() -> Generator[None]:
                yield None
                with events_file.open("a") as output:
                    _ = output.write("function\\n")

            @test()
            async def test_waits() -> None:
                _ = load_fixture(session_resource())
                _ = load_fixture(function_resource())
                _ = await asyncio.to_thread(
                    Path({str(started_file)!r}).write_text, "started"
                )
                await asyncio.Event().wait()
        """),
        name="test_parent_cancellation",
    )

    run_task = asyncio.create_task(run_tests_programmatic([FilterItem(str(test_file))]))
    while not await asyncio.to_thread(started_file.exists):  # noqa: ASYNC110
        await asyncio.sleep(0)

    _ = run_task.cancel()
    with assert_raises(asyncio.CancelledError):
        _ = await run_task

    assert_eq(events_file.read_text().splitlines(), ["function", "session"])


@test()
def test_hanging_async_function_teardown_is_bounded_and_attributed() -> None:
    """A hanging async function teardown cannot block the CLI indefinitely."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import AsyncGenerator

            from snektest import fixture, load_fixture, test

            @fixture
            async def hanging_fixture() -> AsyncGenerator[None]:
                yield None
                await asyncio.Event().wait()

            @test()
            async def test_uses_hanging_fixture() -> None:
                _ = await load_fixture(hanging_fixture())
        """),
        name="test_hanging_function_teardown",
    )

    result = run_test_subprocess(test_file, "--timeout", "0.05", timeout=5)

    assert_eq(result["passed"], 1)
    assert_eq(result["fixture_teardown_failed"], 1)
    assert_eq(
        result["tests"][0]["fixture_teardown_failures"][0]["fixture_name"],
        "hanging_fixture",
    )
    assert_eq(
        result["tests"][0]["fixture_teardown_failures"][0]["exception"]["type"],
        "FixtureTeardownTimeoutError",
    )
    assert_eq(result["returncode"], 1)


@test()
def test_hanging_async_session_teardown_is_bounded_and_attributed() -> None:
    """A hanging async session teardown cannot block the CLI indefinitely."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import AsyncGenerator

            from snektest import fixture, load_fixture, test

            @fixture(scope="session")
            async def hanging_session_fixture() -> AsyncGenerator[None]:
                yield None
                await asyncio.Event().wait()

            @test()
            async def test_uses_hanging_session_fixture() -> None:
                _ = await load_fixture(hanging_session_fixture())
        """),
        name="test_hanging_session_teardown",
    )

    result = run_test_subprocess(test_file, "--timeout", "0.05", timeout=5)

    assert_eq(result["passed"], 1)
    assert_eq(result["session_teardown_failed"], 1)
    assert_eq(
        result["session_teardown_failures"][0]["fixture_name"],
        "hanging_session_fixture",
    )
    assert_eq(
        result["session_teardown_failures"][0]["exception"]["type"],
        "FixtureTeardownTimeoutError",
    )
    assert_eq(result["returncode"], 1)


@test()
def test_session_fixture_teardown_failure() -> None:
    """Test that session fixture teardown failures are reported."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture(scope="session")
            def session_fixture_with_failing_teardown() -> Generator[None]:
                yield None
                msg = "failing teardown"
                raise ValueError(msg)

            @test()
            def test_with_bad_session_fixture() -> None:
                _ = load_fixture(session_fixture_with_failing_teardown())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["session_teardown_failed"], 1)


@test()
def test_json_retains_session_teardown_output_and_warnings() -> None:
    """Structured results retain session teardown diagnostics."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import warnings
            from collections.abc import Generator

            from snektest import fixture, load_fixture, test

            @fixture(scope="session")
            def noisy_session_fixture() -> Generator[None]:
                yield None
                print("session teardown output")
                warnings.warn("session teardown warning", stacklevel=1)
                raise ValueError("session teardown failure")

            @test()
            def test_uses_session_fixture() -> None:
                _ = load_fixture(noisy_session_fixture())
        """),
        name="test_session_teardown_diagnostics",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["session_teardown_output"], "session teardown output\n")
    assert_in(
        "session teardown warning", "\n".join(result["session_teardown_warnings"])
    )
    assert_eq(result["returncode"], 1)


@test()
def test_good_fixture_no_errors() -> None:
    """Test that good fixtures don't report any errors."""
    tmp_dir = load_fixture(tmp_dir_fixture())

    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            from snektest import fixture, load_fixture, test

            @fixture
            def good_fixture() -> Generator[None]:
                yield None

            @test()
            def test_with_good_fixture() -> None:
                _ = load_fixture(good_fixture())
        """),
    )

    result = run_test_subprocess(test_file)
    assert_eq(result["passed"], 1)
    assert_eq(result["failed"], 0)
    assert_eq(result["fixture_teardown_failed"], 0)
    assert_eq(result["session_teardown_failed"], 0)
