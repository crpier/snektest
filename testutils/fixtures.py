"""Shared fixtures for snektest's test suite."""

import tempfile
from pathlib import Path

from snektest import SessionFixture


def tmp_dir_fixture() -> SessionFixture[Path]:
    """Fixture that provides a temporary directory for tests.

    Yields:
        Path to temporary directory
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)
