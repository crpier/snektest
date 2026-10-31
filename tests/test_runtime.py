"""Exercise the private runtime seam against real native engines, not fakes."""

lazy import asyncio
lazy import json
lazy import subprocess
lazy import sys
lazy import threading
lazy from contextvars import ContextVar

import trio.lowlevel
lazy import trio

lazy from snektest import (
    BadRequestError,
    Param,
    TestTimeoutError,
    assert_eq,
    assert_false,
    assert_is,
    assert_raises,
    assert_true,
    test,
)
lazy from snektest.execution import _await_test_body
lazy from snektest.runtime import (
    Backend,
    backend_name,
    cancelled_errors,
    current_backend,
    run,
    run_sync,
    runtime_scope,
    timeout,
)

_BACKENDS: list[Param[Backend]] = [
    Param(value="asyncio", name="asyncio"),
    Param(value="trio", name="trio"),
]


async def _pause(backend: Backend) -> None:
    if backend == "asyncio":
        await asyncio.sleep(3600)
    else:
        await trio.sleep_forever()


def _primitive_probe(backend: Backend) -> None:
    context = ContextVar("runtime_probe", default="outside")
    _ = context.set("loader")
    owner_thread = threading.get_ident()

    async def exercise() -> None:
        assert_eq(current_backend(), backend)
        assert_eq(context.get(), "loader")
        _ = context.set("body")

        def blocking(*, suffix: str) -> tuple[str, bool]:
            observed = context.get()
            _ = context.set("thread-local")
            return observed + suffix, threading.get_ident() != owner_thread

        assert_eq(await run_sync(blocking, suffix="-copy"), ("body-copy", True))
        assert_eq(context.get(), "body")
        limit = timeout(0.005)
        with assert_raises(TimeoutError):
            async with limit:
                await _pause(backend)
        assert_true(limit.expired())

        ordinary = TimeoutError("application timeout")
        limit = timeout(60)
        with assert_raises(TimeoutError) as caught:
            async with limit:
                raise ordinary
        assert_is(caught.exception, ordinary)
        assert_false(limit.expired())

        async with timeout(None):
            assert_eq(await run_sync(lambda: 42), 42)

        other: Backend = "trio" if backend == "asyncio" else "asyncio"
        with (
            assert_raises(BadRequestError, match="matching running"),
            runtime_scope(other),
        ):
            pass
        assert_eq(current_backend(), backend)
        with assert_raises(BadRequestError, match="inside a running"):
            _ = run(exercise, backend=backend)
        assert_eq(
            cancelled_errors(),
            (asyncio.CancelledError,) if backend == "asyncio" else (trio.Cancelled,),
        )

    run(exercise, backend=backend)
    assert_eq(current_backend(), "asyncio")
    assert_eq(context.get(), "loader")


@test(_BACKENDS, mark="medium")
async def test_native_runtime_primitives(backend: Backend) -> None:
    await asyncio.to_thread(_primitive_probe, backend)


def _body_probe(backend: Backend) -> None:
    async def exercise() -> None:
        with assert_raises(TestTimeoutError):
            await _await_test_body(_pause(backend), 0.005)

        async def user_timeout() -> None:
            message = "application timeout"
            raise TimeoutError(message)

        with assert_raises(TimeoutError, match="application timeout"):
            await _await_test_body(user_timeout(), 10)

        async def no_limit() -> None:
            assert_eq(current_backend(), backend)

        await _await_test_body(no_limit(), None)

    run(exercise, backend=backend)


@test(_BACKENDS, mark="medium")
async def test_body_timeout_preserves_native_runtime_and_user_errors(
    backend: Backend,
) -> None:
    await asyncio.to_thread(_body_probe, backend)


def _cancellation_probe(backend: Backend) -> None:
    async def exercise() -> None:
        limit = timeout(60)
        if backend == "asyncio":

            async def cancel_parent() -> None:
                async with limit:
                    task = asyncio.current_task()
                    if task is not None:
                        _ = task.cancel()
                    await asyncio.sleep(0)

            with assert_raises(asyncio.CancelledError):
                await cancel_parent()
        else:
            with trio.CancelScope() as parent:
                parent.cancel()
                try:
                    async with limit:
                        await trio.lowlevel.checkpoint()
                except trio.Cancelled:
                    assert_false(limit.expired())
                    raise
            assert_true(parent.cancelled_caught)
        assert_false(limit.expired())

    run(exercise, backend=backend)


@test(_BACKENDS, mark="medium")
async def test_parent_cancellation_is_not_a_body_timeout(backend: Backend) -> None:
    await asyncio.to_thread(_cancellation_probe, backend)


@test(mark="medium")
def test_backend_names_are_exact_and_scopes_require_a_running_host() -> None:
    assert_eq(backend_name("asyncio"), "asyncio")
    assert_eq(backend_name("trio"), "trio")
    with assert_raises(BadRequestError, match="Unsupported async backend"):
        _ = backend_name("auto")
    with assert_raises(BadRequestError, match="Unsupported async backend"):
        _ = backend_name("Trio")

    # This test itself runs on asyncio. A separate ordinary thread has no host.
    errors: list[str] = []

    def no_host() -> None:
        try:
            with runtime_scope("asyncio"):
                pass
        except BadRequestError as error:
            errors.append(str(error))

    thread = threading.Thread(target=no_host)
    thread.start()
    thread.join()
    assert_eq(len(errors), 1)


@test(mark="slow")
def test_default_runtime_does_not_realize_optional_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; from snektest.runtime import run, timeout; exec('async def body():\\n async with timeout(1):\\n  return 42'); value = run(body, backend='asyncio'); print(json.dumps([value, 'trio' in sys.modules, 'anyio' in sys.modules]))",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert_eq(result.returncode, 0, msg=result.stdout + result.stderr)
    assert_eq(json.loads(result.stdout), [42, False, False])
