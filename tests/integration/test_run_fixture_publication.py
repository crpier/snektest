"""Publication outcomes survive reader handoffs and execution-worker replacement."""

import asyncio
from textwrap import dedent

from snektest import Param, assert_eq, assert_in, load_fixture, test
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file, run_test_subprocess


@test(
    [Param("published", "published"), Param("unavailable", "unavailable")], mark="slow"
)
async def test_replacement_worker_restores_publication_outcome(outcome: str) -> None:
    """Replacement receives fresh copies or the original cached decode failure."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import os
            from collections.abc import Generator
            from multiprocessing import current_process
            from snektest import FixtureError, assert_eq, assert_in, assert_isinstance, assert_raises, fixture, load_fixture, test

            def decode() -> list[str]:
                if {outcome!r} == "unavailable" and current_process().name.endswith("worker-1"):
                    raise FixtureError("original worker rejected descriptor")
                return ["ready"]

            class Descriptor:
                def __reduce__(self) -> tuple[object, tuple[()]]:
                    return decode, ()

            @fixture(scope="run")
            def shared() -> Generator[object]:
                print("setup once")
                yield Descriptor()
                print("teardown once")

            @test(mark="slow")
            def test_crash() -> None:
                if {outcome!r} == "unavailable":
                    with assert_raises(FixtureError):
                        _ = load_fixture(shared())
                else:
                    descriptor = assert_isinstance(load_fixture(shared()), list)
                    descriptor.append("old worker mutation")
                os._exit(7)

            @test(mark="slow")
            def test_replacement() -> None:
                if {outcome!r} == "unavailable":
                    with assert_raises(FixtureError) as caught:
                        _ = load_fixture(shared())
                    assert_in("original worker rejected descriptor", str(caught.exception))
                else:
                    assert_eq(load_fixture(shared()), ["ready"])
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--workers", "1", timeout=20
    )

    assert_eq(result["returncode"], 1)
    assert_eq([case["status"] for case in result["tests"]], ["error", "passed"])
    assert_in("exited unexpectedly", result["tests"][0]["exception"]["message"])
    assert_eq(result["run_teardown_output"], "setup once\nteardown once\n")
    assert_eq(result["stderr"], "")


@test([Param("reject", "reject"), Param("stall", "stall")], mark="slow")
async def test_replacement_decode_failure_stops_the_run(mode: str) -> None:
    """A replacement that cannot restore a descriptor never executes later cases."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent(f"""\
            import os
            import time
            from collections.abc import Generator
            from multiprocessing import current_process
            from snektest import FixtureError, fixture, load_fixture, test

            def decode() -> str:
                if current_process().name.endswith("worker-2"):
                    if {mode!r} == "reject":
                        raise FixtureError("replacement rejected descriptor")
                    time.sleep(4)
                return "ready"

            class Descriptor:
                def __reduce__(self) -> tuple[object, tuple[()]]:
                    return decode, ()

            @fixture(scope="run")
            def shared() -> Generator[object]:
                yield Descriptor()

            @test(mark="slow")
            def test_crash() -> None:
                _ = load_fixture(shared())
                os._exit(7)

            @test(mark="fast")
            def test_not_dispatched() -> None:
                raise FixtureError("replacement received work before restoration")
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--workers", "1", "--timeout", "2", timeout=20
    )

    assert_eq(result["returncode"], 2, msg=result["stdout"])
    assert_eq(result["error"]["category"], "infrastructure")
    assert_in("worker 2", result["error"]["message"])
    assert_in(
        "replacement rejected descriptor" if mode == "reject" else "within 2s",
        result["error"]["message"],
    )
    assert_eq(result["stderr"], "")


@test(mark="slow")
async def test_decode_error_with_broken_message_remains_a_fixture_failure() -> None:
    """A broken decoder diagnostic cannot corrupt publication or stop unrelated work."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent("""\
            from collections.abc import Generator
            from snektest import FixtureError, fixture, load_fixture, test

            class BrokenDecode(FixtureError):
                def __str__(self) -> str:
                    raise FixtureError("broken message formatter")

            def decode() -> object:
                raise BrokenDecode()

            class Descriptor:
                def __reduce__(self) -> tuple[object, tuple[()]]:
                    return decode, ()

            @fixture(scope="run")
            def shared() -> Generator[object]:
                yield Descriptor()

            @test(mark="slow")
            def test_descriptor() -> None:
                _ = load_fixture(shared())

            @test(mark="fast")
            def test_unrelated() -> None:
                pass
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--workers", "2", timeout=20
    )

    assert_eq(result["returncode"], 1, msg=result["stdout"])
    assert_eq([case["status"] for case in result["tests"]], ["error", "passed"])
    assert_in("BrokenDecode", result["tests"][0]["exception"]["message"])
    assert_in("str failed: FixtureError", result["tests"][0]["exception"]["message"])
    assert_eq(result["stderr"], "")


@test(mark="slow")
async def test_one_worker_decode_rejection_discards_every_worker_copy() -> None:
    """No requester may observe a descriptor rejected by another worker."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent("""\
            from collections.abc import Generator
            from multiprocessing import current_process
            from snektest import FixtureError, Param, assert_in, assert_raises, fixture, load_fixture, test

            def decode() -> str:
                if current_process().name.endswith("worker-2"):
                    raise FixtureError("second worker rejected descriptor")
                return "staged successfully"

            class Descriptor:
                def __reduce__(self) -> tuple[object, tuple[()]]:
                    return decode, ()

            @fixture(scope="run")
            def shared() -> Generator[object]:
                yield Descriptor()

            @test([Param(1, "first"), Param(2, "second")], mark="slow")
            def test_cannot_observe_rejected_copy(requester: int) -> None:
                with assert_raises(FixtureError) as caught:
                    _ = load_fixture(shared())
                assert_in("second worker rejected descriptor", str(caught.exception))
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--workers", "2", timeout=20
    )

    assert_eq(result["returncode"], 0, msg=result["stdout"])
    assert_eq(result["passed"], 2)
    assert_eq(result["stderr"], "")


@test(mark="slow")
async def test_cross_loading_a_released_batch_preserves_worker_caches() -> None:
    """A requester can ask for another descriptor while its release is in transit."""
    directory = load_fixture(tmp_dir_fixture())
    test_file = await asyncio.to_thread(
        create_test_file,
        directory,
        dedent("""\
            from collections.abc import Generator
            from snektest import assert_eq, assert_is, fixture, load_fixture, test

            observed: dict[str, list[str]] = {}

            @fixture(scope="run")
            def first() -> Generator[list[str]]:
                yield ["first"]

            @fixture(scope="run")
            def second() -> Generator[list[str]]:
                yield ["second"]

            @test(mark="fast")
            def test_first() -> None:
                observed["first"] = load_fixture(first())
                observed["first"].append("local mutation")
                observed["second"] = load_fixture(second())
                assert_eq(observed["second"], ["second"])

            @test(mark="fast")
            def test_second() -> None:
                observed["second"] = load_fixture(second())
                observed["second"].append("local mutation")
                observed["first"] = load_fixture(first())
                assert_eq(observed["first"], ["first"])

            @test(mark="fast")
            def test_cache_survived_release() -> None:
                assert_is(load_fixture(first()), observed["first"])
                assert_is(load_fixture(second()), observed["second"])
        """),
    )

    result = await asyncio.to_thread(
        run_test_subprocess, test_file, "--workers", "2", timeout=20
    )

    assert_eq(result["returncode"], 0, msg=result["stdout"] + result["stderr"])
    assert_eq(result["passed"], 3)
    assert_eq(result["stderr"], "")
