"""Subprocess regressions for thread and unraisable failure observability."""

from textwrap import dedent

from snektest import Param, assert_eq, assert_in, assert_true, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [
        Param(value=(), name="local"),
        Param(value=("--workers", "1"), name="worker"),
    ],
    mark="slow",
)
def test_joined_thread_exception_is_an_error(arguments: tuple[str, ...]) -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import threading

            from snektest import test


            def break_in_thread() -> None:
                raise RuntimeError("thread boom")


            @test(mark="medium")
            def test_thread_failure() -> None:
                worker = threading.Thread(
                    name="broken-worker",
                    target=break_in_thread,
                )
                worker.start()
                worker.join()
        """),
        name="test_joined_thread_error",
    )

    result = run_test_subprocess(test_file, *arguments, timeout=2)
    test_result = result["tests"][0]

    assert_eq(result["returncode"], 1)
    assert_eq(test_result["status"], "error")
    assert_eq(test_result["exception"]["type"], "RuntimeError")
    assert_in("broken-worker", test_result["exception"]["message"])
    assert_in("thread boom", test_result["exception"]["message"])
    background = test_result["background_failures"][0]
    assert_eq(background["origin"], "thread")
    assert_eq(background["label"], "broken-worker")
    assert_eq(background["exception"]["traceback"][-1]["function"], "break_in_thread")


@test(mark="slow")
def test_unraisable_exception_is_an_error() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import gc

            from snektest import test


            class BrokenFinalizer:
                def __del__(self) -> None:
                    raise RuntimeError("finalizer boom")


            @test(mark="medium")
            def test_unraisable() -> None:
                value = BrokenFinalizer()
                del value
                _ = gc.collect()
        """),
        name="test_unraisable_error",
    )

    result = run_test_subprocess(test_file)
    test_result = result["tests"][0]

    assert_eq(result["returncode"], 1)
    assert_eq(test_result["status"], "error")
    assert_eq(test_result["exception"]["type"], "RuntimeError")
    assert_in("unraisable", test_result["exception"]["message"].lower())
    assert_in("finalizer boom", test_result["exception"]["message"])
    background = test_result["background_failures"][0]
    assert_eq(background["origin"], "unraisable")
    assert_eq(background["exception"]["traceback"][-1]["function"], "__del__")


@test(mark="slow")
def test_live_non_daemon_thread_fails_test_without_hanging_runner() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import threading
            import time

            from snektest import test


            @test(mark="medium")
            def test_thread_leak() -> None:
                worker = threading.Thread(
                    name="leaked-worker",
                    target=lambda: time.sleep(0.2),
                )
                worker.start()
        """),
        name="test_thread_leak",
    )

    result = run_test_subprocess(test_file, timeout=1)
    test_result = result["tests"][0]

    assert_eq(result["returncode"], 1)
    assert_eq(test_result["status"], "failed")
    assert_eq(test_result["exception"]["type"], "AssertionFailure")
    assert_in("leaked-worker", test_result["exception"]["message"])


@test(mark="slow")
def test_supported_thread_lifecycles_do_not_fail() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import asyncio
            from collections.abc import Generator
            import threading
            import time

            from snektest import assert_eq, fixture, load_fixture, test


            @fixture
            def value() -> Generator[int]:
                yield 42


            @test(mark="medium")
            async def test_to_thread_context() -> None:
                loaded = await asyncio.to_thread(lambda: load_fixture(value()))
                assert_eq(loaded, 42)


            @test(mark="medium")
            def test_joined_thread() -> None:
                worker = threading.Thread(target=lambda: None)
                worker.start()
                worker.join()


            @test(mark="medium")
            def test_daemon_thread_is_ignored() -> None:
                worker = threading.Thread(
                    daemon=True,
                    target=lambda: time.sleep(0.5),
                )
                worker.start()
        """),
        name="test_supported_threads",
    )

    result = run_test_subprocess(test_file)

    assert_eq(result["returncode"], 0)
    assert_eq(result["passed"], 3)


@test(mark="slow")
def test_raw_thread_fixture_error_is_visible() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            from collections.abc import Generator
            import threading

            from snektest import fixture, load_fixture, test


            @fixture
            def value() -> Generator[int]:
                yield 42


            @test(mark="medium")
            def test_raw_thread_context() -> None:
                worker = threading.Thread(
                    name="raw-fixture-worker",
                    target=lambda: load_fixture(value()),
                )
                worker.start()
                worker.join()
        """),
        name="test_raw_thread_fixture",
    )

    result = run_test_subprocess(test_file)
    test_result = result["tests"][0]

    assert_eq(result["returncode"], 1)
    assert_eq(test_result["status"], "error")
    assert_in("raw-fixture-worker", test_result["exception"]["message"])


@test(mark="slow")
def test_late_failure_from_prior_leak_is_not_misattributed() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import threading

            from snektest import test

            release = threading.Event()
            workers: list[threading.Thread] = []


            def fail_late() -> None:
                _ = release.wait(timeout=1)
                raise RuntimeError("late boom")


            @test(mark="medium")
            def test_leaks_thread() -> None:
                worker = threading.Thread(name="late-worker", target=fail_late)
                workers.append(worker)
                worker.start()


            @test(mark="medium")
            def test_releases_prior_thread() -> None:
                release.set()
                workers[0].join()
        """),
        name="test_late_thread_failure",
    )

    result = run_test_subprocess(test_file, timeout=2)

    assert_eq(result["returncode"], 1)
    assert_eq(result["failed"], 1)
    assert_eq(result["errors"], 0)
    assert_eq(result["tests"][0]["status"], "failed")
    assert_eq(result["tests"][1]["status"], "passed")


@test(mark="slow")
def test_body_failure_keeps_structured_background_failure() -> None:
    tmp_dir = load_fixture(tmp_dir_fixture())
    test_file = create_test_file(
        tmp_dir,
        dedent("""
            import threading

            from snektest import assert_eq, test


            def break_in_thread() -> None:
                raise RuntimeError("secondary boom")


            @test(mark="medium")
            def test_two_failures() -> None:
                worker = threading.Thread(
                    name="secondary-worker",
                    target=break_in_thread,
                )
                worker.start()
                worker.join()
                assert_eq(1, 2)
        """),
        name="test_body_and_thread_failure",
    )

    result = run_test_subprocess(test_file)
    test_result = result["tests"][0]
    background = test_result["background_failures"][0]

    assert_eq(result["returncode"], 1)
    assert_eq(test_result["status"], "failed")
    assert_eq(test_result["exception"]["type"], "AssertionFailure")
    assert_eq(background["origin"], "thread")
    assert_eq(background["label"], "secondary-worker")
    assert_eq(background["exception"]["type"], "RuntimeError")
    assert_true("secondary boom" in background["exception"]["message"])
