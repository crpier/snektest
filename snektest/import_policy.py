"""Scoped eager initialization for collection and optional contract engines."""

lazy import sys
lazy from collections.abc import Generator
lazy from contextlib import contextmanager
lazy from threading import RLock

_INITIALIZATION_LOCK = RLock()
_ENGINE_PACKAGES = (
    "hypothesis",
    "hypothesis_graphql",
    "hypothesis_jsonschema",
    "schemathesis",
    "graphql",
)


@contextmanager
def initializing_imports() -> Generator[None]:
    """Complete synchronous collection/engine initialization without changing mode.

    Test-module imports must finish during collection. The supported engines
    also need eager internal registration under forced-lazy mode. Other imports
    retain the caller's filter, which is restored on every exit. Keep this scope
    synchronous: it must not span an await or a test/fixture lifetime.
    """
    with _INITIALIZATION_LOCK:
        previous = sys.get_lazy_imports_filter()

        def allow_lazy(
            importer: str | None, name: str, fromlist: tuple[str, ...] | None
        ) -> bool:
            if importer is not None and (
                importer.startswith("_snektest_collection_")
                or any(
                    importer == package or importer.startswith(f"{package}.")
                    for package in _ENGINE_PACKAGES
                )
            ):
                return False
            return previous(importer, name, fromlist) if previous is not None else True

        sys.set_lazy_imports_filter(allow_lazy)
        try:
            yield
        finally:
            sys.set_lazy_imports_filter(previous)
