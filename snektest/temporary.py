"""Owned temporary paths and off-loop, ownership-checked recursive cleanup."""

lazy import shutil
lazy import tempfile
lazy from dataclasses import dataclass
lazy from os import PathLike
lazy from pathlib import Path
lazy from typing import Literal

lazy from snektest.diagnostics import snapshot_exception
lazy from snektest.fixtures import current_registry
lazy from snektest.models import (
    BadRequestError,
    FixtureError,
    RetainedDirectory,
    TeardownFailure,
    UnreachableError,
)
lazy from snektest.runtime import run_sync
lazy from snektest.task_cleanup import defer_cancellation


@dataclass(frozen=True)
class _OwnedDirectory:
    path: Path
    device: int
    inode: int
    retention: Literal["never", "on-failure", "always"]


def _remove_owned(directory: _OwnedDirectory) -> None:
    try:
        current = directory.path.lstat()
    except FileNotFoundError:
        return
    if directory.path.is_symlink() or (current.st_dev, current.st_ino) != (
        directory.device,
        directory.inode,
    ):
        msg = f"Refusing to remove replaced temporary directory: {directory.path}"
        raise FixtureError(msg)
    shutil.rmtree(directory.path)


class TemporaryDirectories:
    """Registry-owned state; callers receive only paths, not cleanup controls."""

    def __init__(self) -> None:
        self._function: list[_OwnedDirectory] = []
        self._session: list[_OwnedDirectory] = []
        self._failed = False
        self._active_test = False
        self._closed = False
        self.retained_session: tuple[RetainedDirectory, ...] = ()

    def start_test(self) -> None:
        self._active_test = True

    def allocate(
        self,
        base: str | PathLike[str] | None,
        retention: Literal["never", "on-failure", "always"],
        *,
        session: bool,
    ) -> Path:
        if self._closed or not (session or self._active_test):
            msg = "temporary_directory requires an active test or session fixture"
            raise FixtureError(msg)
        path = Path(tempfile.mkdtemp(prefix="sqt-", dir=base)).resolve()
        identity = path.lstat()
        directories = self._session if session else self._function
        directories.append(
            _OwnedDirectory(path, identity.st_dev, identity.st_ino, retention)
        )
        return path

    @defer_cancellation
    async def finish_function(
        self, *, failed: bool
    ) -> tuple[list[TeardownFailure], tuple[RetainedDirectory, ...]]:
        self._active_test = False
        self._failed |= failed
        directories, self._function = self._function, []
        failures, retained = await self._finish(
            directories, failed=failed, scope="function"
        )
        self._failed |= bool(failures)
        return failures, retained

    @defer_cancellation
    async def finish_session(self, *, failed: bool) -> list[TeardownFailure]:
        self._closed = True
        directories, self._session = self._session, []
        failures, self.retained_session = await self._finish(
            directories, failed=failed or self._failed, scope="session"
        )
        return failures

    @staticmethod
    async def _finish(
        directories: list[_OwnedDirectory],
        *,
        failed: bool,
        scope: Literal["function", "session"],
    ) -> tuple[list[TeardownFailure], tuple[RetainedDirectory, ...]]:
        failures: list[TeardownFailure] = []
        retained: list[RetainedDirectory] = []
        for directory in reversed(directories):
            if directory.retention == "always" or (
                directory.retention == "on-failure" and failed
            ):
                retained.append(
                    RetainedDirectory(
                        str(directory.path),
                        directory.retention,
                        "always" if directory.retention == "always" else "failure",
                        scope,
                    )
                )
                continue
            try:
                await run_sync(_remove_owned, directory)
            except Exception as error:
                failed = True
                failures.append(
                    TeardownFailure(
                        exception=snapshot_exception(
                            type(error), error, error.__traceback__
                        ),
                        fixture_name="temporary_directory",
                    )
                )
                retained.append(
                    RetainedDirectory(
                        str(directory.path),
                        directory.retention,
                        "cleanup_error",
                        scope,
                    )
                )
        return failures, tuple(retained)


def temporary_directory(
    *,
    base: str | PathLike[str] | None = None,
    retention: Literal["never", "on-failure", "always"] = "never",
) -> Path:
    """Allocate a private absolute path owned by the current test/session setup.

    The base must already exist; None uses Python's normal temporary base.
    Retention is never (default), on-failure, or always. Resource fixtures and
    task/background classification finish before directory policy. Only paths,
    not contents, enter completed console/JSON/programmatic results. Removal runs
    in a thread: cancellation waits, but hung filesystem IO needs an outer timeout.
    Run fixtures and standalone context-manager ownership are unsupported.
    """
    if retention not in ("never", "on-failure", "always"):
        msg = "temporary_directory retention must be 'never', 'on-failure', or 'always'"
        raise BadRequestError(msg)
    try:
        registry = current_registry()
    except UnreachableError:
        msg = "temporary_directory requires an active snektest run"
        raise FixtureError(msg) from None
    return registry.allocate_temporary_directory(base=base, retention=retention)
