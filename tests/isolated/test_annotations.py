from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from snektest import assert_eq, assert_raises, test
from snektest.annotations import PyFilePath


@test()
def test_pyfilepath_json_schema_includes_format_and_string_type() -> None:
    schema = TypeAdapter(PyFilePath).json_schema()
    assert_eq(schema["type"], "string")
    assert_eq(schema["format"], "file-path")


@test()
def test_validate_pyfilepath_rejects_non_file() -> None:
    with assert_raises(ValidationError):
        TypeAdapter(PyFilePath).validate_python(Path("."))
