"""Load typed Snektest defaults from the nearest project configuration."""

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tomllib import TOMLDecodeError, load
from typing import Any, Literal, cast

from snektest.models import BadRequestError

_CONFIG_KEYS = {
    "capture_output",
    "json_output",
    "junit_output",
    "mark",
    "test_paths",
    "timeout",
}
"""Accepted keys in the project configuration table."""


@dataclass(frozen=True)
class ProjectConfig:
    """Validated defaults read from one `[tool.snektest]` table."""

    capture_output: bool = True
    json_output: bool = False
    junit_output: str | None = None
    mark: Literal["fast", "medium", "slow"] | None = None
    test_paths: tuple[str, ...] = ()
    timeout: float | Literal[False] | None = None


def load_project_config(*, start: Path) -> ProjectConfig:  # noqa: C901
    """Read the nearest `pyproject.toml` and validate Snektest's table."""
    working_directory = start.resolve()
    for directory in (working_directory, *working_directory.parents):
        config_path = directory / "pyproject.toml"
        if not config_path.is_file():
            continue
        try:
            with config_path.open("rb") as config_file:
                document = load(config_file)
        except (OSError, TOMLDecodeError) as error:
            message = f"Could not read project configuration `{config_path}`: {error}"
            raise BadRequestError(message) from error
        tool = document.get("tool", {})
        table: Any = tool.get("snektest", {}) if isinstance(tool, dict) else {}
        if not isinstance(table, dict):
            message = f"`tool.snektest` in `{config_path}` must be a table"
            raise BadRequestError(message)
        table = cast("dict[str, Any]", table)
        unknown_keys = sorted(set(table) - _CONFIG_KEYS)
        if unknown_keys:
            message = f"Unknown `tool.snektest` key: {unknown_keys[0]}"
            raise BadRequestError(message)
        raw_test_paths: Any = table.get("test_paths", [])
        if not isinstance(raw_test_paths, list) or any(
            not isinstance(path, str) or not path.strip() for path in raw_test_paths
        ):
            message = "`tool.snektest.test_paths` must be a list of non-empty strings"
            raise BadRequestError(message)
        raw_capture_output: Any = table.get("capture_output", True)
        if not isinstance(raw_capture_output, bool):
            message = "`tool.snektest.capture_output` must be true or false"
            raise BadRequestError(message)
        raw_json_output: Any = table.get("json_output", False)
        if not isinstance(raw_json_output, bool):
            message = "`tool.snektest.json_output` must be true or false"
            raise BadRequestError(message)
        raw_junit_output: Any = table.get("junit_output")
        if raw_junit_output is not None and (
            not isinstance(raw_junit_output, str) or not raw_junit_output.strip()
        ):
            message = "`tool.snektest.junit_output` must be a non-empty string"
            raise BadRequestError(message)
        raw_mark: Any = table.get("mark")
        if raw_mark is not None and (
            not isinstance(raw_mark, str) or raw_mark not in {"fast", "medium", "slow"}
        ):
            message = "`tool.snektest.mark` must be fast, medium, or slow"
            raise BadRequestError(message)
        raw_timeout: Any = table.get("timeout")
        if (
            raw_timeout is not None
            and raw_timeout is not False
            and (
                isinstance(raw_timeout, bool)
                or not isinstance(raw_timeout, (int, float))
                or not isfinite(raw_timeout)
                or raw_timeout <= 0
            )
        ):
            message = (
                "`tool.snektest.timeout` must be a finite positive number or false"
            )
            raise BadRequestError(message)
        return ProjectConfig(
            capture_output=raw_capture_output,
            json_output=raw_json_output,
            junit_output=(
                os.path.relpath(directory / raw_junit_output, working_directory)
                if raw_junit_output is not None
                else None
            ),
            mark=raw_mark,
            test_paths=tuple(
                os.path.relpath(directory / path, working_directory)
                for path in raw_test_paths
            ),
            timeout=(
                False
                if raw_timeout is False
                else float(raw_timeout)
                if raw_timeout is not None
                else None
            ),
        )
    return ProjectConfig()


__all__ = ["ProjectConfig", "load_project_config"]
