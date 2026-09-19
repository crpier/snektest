"""Run-fixture publication, replay, and exclusive worker-side reader ownership.

The coordinator publishes only while scheduler reads are quiescent. This module
owns the complete stage/release batch so no worker resumes while another fixture
in that batch still needs acknowledgements. Process and fixture lifetimes remain
with their existing owners.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pickle import loads
from typing import Any, Protocol, cast

from snektest.annotations import AsyncFixture, Coroutine, Fixture
from snektest.decorators import RunFixtureIdentity
from snektest.diagnostics import snapshot_exception
from snektest.models import FixtureError, RunInfrastructureError, TestResult


class ProcessConnection(Protocol):
    """Operations shared by Unix `Connection` and Windows `PipeConnection`."""

    def close(self) -> None: ...

    def recv(self) -> object: ...

    def send(self, obj: Any) -> None: ...


@dataclass(frozen=True)
class _RunFixtureRequested:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class LoadRunFixture:
    identity: RunFixtureIdentity
    setup_timeout: float | None


@dataclass(frozen=True)
class RunFixtureLoaded:
    identity: RunFixtureIdentity
    output: str
    payload: bytes


@dataclass(frozen=True)
class RunFixtureFailed:
    identity: RunFixtureIdentity
    message: str
    output: str = ""
    interruption: str | None = None
    exit_code: int | str | None = None


@dataclass(frozen=True)
class _StageRunFixture:
    identity: RunFixtureIdentity
    payload: bytes


@dataclass(frozen=True)
class _RunFixtureStageAck:
    identity: RunFixtureIdentity
    message: str | None = None


@dataclass(frozen=True)
class _CommitRunFixture:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _DiscardRunFixture:
    identity: RunFixtureIdentity


@dataclass(frozen=True)
class _RunFixtureUnavailable:
    identity: RunFixtureIdentity
    message: str


class RunFixturePublication:
    """Own one run's publication outcomes and queued request batches.

    The scheduler yields exclusive use of the supplied connections during
    `publish` and `restore`. It need not know how descriptors are staged,
    released, cached, or replayed. Fixture setup still belongs to the host.
    """

    def __init__(self, host: ProcessConnection, *, timeout: float | None) -> None:
        self._host: ProcessConnection = host
        self._timeout: float | None = timeout
        self._descriptors: dict[RunFixtureIdentity, bytes] = {}
        self._failures: dict[RunFixtureIdentity, str] = {}
        self._outputs: list[str] = []
        self._requests: dict[RunFixtureIdentity, None] = {}
        self._interruption: RunFixtureFailed | None = None

    @property
    def pending(self) -> bool:
        return bool(self._requests)

    @property
    def interrupted(self) -> bool:
        return self._interruption is not None

    @property
    def output(self) -> str:
        return "".join(self._outputs)

    def request(self, message: object) -> bool:
        """Recognize and deduplicate a request without losing arrival order."""
        if not isinstance(message, _RunFixtureRequested):
            return False
        self._requests[message.identity] = None
        return True

    async def publish(self, connections: Sequence[ProcessConnection]) -> None:
        """Stage all queued descriptors before releasing any requesting worker."""
        requests = tuple(self._requests)
        self._requests.clear()
        releases: list[_CommitRunFixture | _RunFixtureUnavailable] = []
        for identity in requests:
            if self._interruption is not None:
                releases.append(
                    _RunFixtureUnavailable(identity, self._interruption.message)
                )
                continue
            try:
                async with asyncio.timeout(self._timeout):
                    release = await self._stage(identity, connections)
            except TimeoutError:
                msg = (
                    f"Run fixture {identity} publication did not finish "
                    f"within {self._timeout:g}s"
                )
                raise RunInfrastructureError(msg) from None
            if isinstance(release, RunFixtureFailed):
                self._interruption = release
                release = _RunFixtureUnavailable(identity, release.message)
            releases.append(release)
        for release in releases:
            for connection in connections:
                await asyncio.to_thread(connection.send, release)

    async def restore(self, connection: ProcessConnection, *, worker_name: str) -> None:
        """Replay published copies and cached failures before a worker gets work."""
        for identity, payload in self._descriptors.items():
            try:
                async with asyncio.timeout(self._timeout):
                    decode_error = await self._stage_copy(connection, identity, payload)
            except TimeoutError:
                msg = (
                    f"{worker_name} did not restore run fixture {identity} "
                    f"within {self._timeout:g}s"
                )
                raise RunInfrastructureError(msg) from None
            if decode_error is not None:
                msg = (
                    f"{worker_name} could not decode run fixture {identity}: "
                    f"{decode_error}"
                )
                raise RunInfrastructureError(msg)
            await asyncio.to_thread(connection.send, _CommitRunFixture(identity))
        for identity, message in self._failures.items():
            await asyncio.to_thread(
                connection.send, _RunFixtureUnavailable(identity, message)
            )

    def raise_if_interrupted(self) -> None:
        """Re-raise setup interruption after worker and host teardown completes."""
        if self._interruption is None:
            return
        if self._interruption.interruption == "SystemExit":
            raise SystemExit(self._interruption.exit_code)
        if self._interruption.interruption == "KeyboardInterrupt":
            raise KeyboardInterrupt
        raise asyncio.CancelledError(self._interruption.message)

    async def _stage(
        self, identity: RunFixtureIdentity, connections: Sequence[ProcessConnection]
    ) -> _CommitRunFixture | _RunFixtureUnavailable | RunFixtureFailed:
        """Stage one host descriptor everywhere before making any copy visible."""
        if identity in self._descriptors or identity in self._failures:
            if identity in self._descriptors:
                return _CommitRunFixture(identity)
            return _RunFixtureUnavailable(identity, self._failures[identity])
        # Leave half the transaction budget for serialization and fleet staging.
        await asyncio.to_thread(
            self._host.send,
            LoadRunFixture(
                identity,
                setup_timeout=None if self._timeout is None else self._timeout / 2,
            ),
        )
        try:
            host_message = await asyncio.to_thread(self._host.recv)
        except (EOFError, OSError) as exc:
            msg = "fixture host exited during run fixture setup"
            raise RunInfrastructureError(msg) from exc
        if (
            not isinstance(host_message, (RunFixtureLoaded, RunFixtureFailed))
            or host_message.identity != identity
        ):
            msg = "fixture host sent an invalid run fixture response"
            raise RunInfrastructureError(msg)
        if host_message.output:
            self._outputs.append(host_message.output)
        if isinstance(host_message, RunFixtureFailed):
            if host_message.interruption is not None:
                return host_message
            self._failures[identity] = host_message.message
            return _RunFixtureUnavailable(identity, host_message.message)

        acknowledgements = await asyncio.gather(
            *(
                self._stage_copy(connection, identity, host_message.payload)
                for connection in connections
            )
        )
        decode_errors = [message for message in acknowledgements if message is not None]
        if decode_errors:
            message = f"Run fixture {identity} publication failed: {decode_errors[0]}"
            self._failures[identity] = message
            for connection in connections:
                await asyncio.to_thread(connection.send, _DiscardRunFixture(identity))
            return _RunFixtureUnavailable(identity, message)

        self._descriptors[identity] = host_message.payload
        return _CommitRunFixture(identity)

    @staticmethod
    async def _stage_copy(
        connection: ProcessConnection, identity: RunFixtureIdentity, payload: bytes
    ) -> str | None:
        """Live publication and replay require an acknowledgement for this identity."""
        await asyncio.to_thread(connection.send, _StageRunFixture(identity, payload))
        acknowledgement = await asyncio.to_thread(connection.recv)
        if (
            not isinstance(acknowledgement, _RunFixtureStageAck)
            or acknowledgement.identity != identity
        ):
            msg = f"Invalid staging acknowledgement for run fixture {identity}"
            raise RunInfrastructureError(msg)
        return acknowledgement.message


class WorkerRunFixtures:
    """Worker-side descriptor cache driven by coordinator publication messages."""

    def __init__(self, connection: ProcessConnection) -> None:
        self._committed: dict[RunFixtureIdentity, object] = {}
        self._connection: ProcessConnection = connection
        self._failures: dict[RunFixtureIdentity, str] = {}
        self._staged: dict[RunFixtureIdentity, object] = {}
        self._load_lock: threading.Lock = threading.Lock()
        self._pending: dict[RunFixtureIdentity, asyncio.Future[object]] = {}

    def __call__[R](self, handle: Fixture[R] | AsyncFixture[R]) -> R | Coroutine[R]:
        identity = (
            cast("str", getattr(handle.key, "__module__", "")),
            cast("str", getattr(handle.key, "__qualname__", "")),
        )
        if isinstance(handle, AsyncFixture):

            async def load_async() -> R:
                pending = self._pending.get(identity)
                if pending is None:
                    pending = asyncio.get_running_loop().run_in_executor(
                        None, self._load, identity
                    )
                    self._pending[identity] = pending
                # wait does not cancel the shared future or log abandoned shield errors.
                _ = await asyncio.wait({pending})
                return cast("R", pending.result())

            return cast("Coroutine[R]", load_async())
        return cast("R", self._load(identity))

    async def receive_command(self) -> object:
        """Consume publication traffic without exposing it to the worker loop."""
        while True:
            message = await asyncio.to_thread(self._connection.recv)
            if not await asyncio.to_thread(self._process_control, message):
                return message

    async def run_case(self, execution: Coroutine[TestResult]) -> TestResult:
        """Return a result only after all borrowed connection readers finish.

        Even a cancelled fixture waiter leaves an executor thread receiving
        publication messages. Draining before returning prevents that reader
        from stealing the next command or racing the result acknowledgement.
        """
        try:
            return await execution
        finally:
            await self._drain()

    async def _drain(self) -> None:
        """Finish remote readers before the worker main loop receives again.

        Cancelling an async waiter cannot stop its executor thread. Keep the
        underlying futures alive and retrieve their outcomes even without waiters.
        """
        if self._pending:
            _ = await asyncio.gather(*self._pending.values(), return_exceptions=True)
            self._pending.clear()

    def _process_control(self, message: object) -> bool:
        """Apply one publication message, returning whether it was recognized."""
        if isinstance(message, _StageRunFixture):
            try:
                self._staged[message.identity] = loads(message.payload)  # noqa: S301
            except BaseException as exc:
                diagnostic = snapshot_exception(type(exc), exc, exc.__traceback__)
                self._connection.send(
                    _RunFixtureStageAck(
                        message.identity,
                        f"{diagnostic.type_name}: {diagnostic.message}",
                    )
                )
            else:
                self._connection.send(_RunFixtureStageAck(message.identity))
            return True
        if isinstance(message, _CommitRunFixture):
            # A new request can cross a commit already travelling down the pipe.
            # Its cached release must preserve the existing worker-local copy.
            if message.identity not in self._committed:
                self._committed[message.identity] = self._staged.pop(message.identity)
            self._failures.pop(message.identity, None)
            return True
        if isinstance(message, _DiscardRunFixture):
            self._staged.pop(message.identity, None)
            return True
        if isinstance(message, _RunFixtureUnavailable):
            self._staged.pop(message.identity, None)
            self._failures[message.identity] = message.message
            return True
        return False

    def _load(self, identity: RunFixtureIdentity) -> object:
        with self._load_lock:
            if identity in self._failures:
                raise FixtureError(self._failures[identity])
            if identity in self._committed:
                return self._committed[identity]

            self._connection.send(_RunFixtureRequested(identity))
            while True:
                message = self._connection.recv()
                if not self._process_control(message):
                    msg = f"Expected run fixture publication, got {type(message).__name__}"
                    raise FixtureError(msg)
                if (
                    isinstance(message, _CommitRunFixture)
                    and message.identity == identity
                ):
                    return self._committed[identity]
                if (
                    isinstance(message, _RunFixtureUnavailable)
                    and message.identity == identity
                ):
                    raise FixtureError(message.message)
