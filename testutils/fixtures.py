"""Shared fixtures for snektest's test suite."""

import tempfile
from collections.abc import Generator
from pathlib import Path

from snektest import session_fixture


@session_fixture()
def tmp_dir_fixture() -> Generator[Path]:
    """Fixture that provides a temporary directory for tests.

    Yields:
        Path to temporary directory
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)
