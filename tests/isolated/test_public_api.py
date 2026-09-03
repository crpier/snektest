"""Compatibility tests for snektest's deliberate top-level interface."""

from __future__ import annotations

import ast
from collections.abc import Generator
from pathlib import Path
from typing import cast

import snektest
from snektest import (
    Scope,
    assert_eq,
    assert_false,
    assert_is,
    assert_isinstance,
    assert_true,
    fixture,
    test,
)

_PUBLIC_ERRORS = {
    "AssertionFailure",
    "BadRequestError",
    "CollectionError",
    "FixtureError",
    "SchemaGenerationError",
    "SnektestError",
    "TestTimeoutError",
}


def _stub_exports() -> list[str]:
    stub_path = Path(snektest.__file__).with_name("__init__.pyi")
    module = ast.parse(stub_path.read_text())
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in statement.targets
        ):
            return cast("list[str]", ast.literal_eval(statement.value))
    msg = "snektest/__init__.pyi does not declare __all__"
    raise RuntimeError(msg)


@test(mark="fast")
def test_runtime_and_stub_exports_match() -> None:
    """The installed runtime and static interface advertise the same names."""
    assert_eq(snektest.__all__, _stub_exports())
    assert_true(set(snektest.__all__) >= _PUBLIC_ERRORS)


@test(mark="fast")
def test_public_errors_share_conventional_hierarchy() -> None:
    """User-facing framework errors are catchable conventionally and together."""
    base = assert_isinstance(snektest.SnektestError, type)
    for error_name in sorted(_PUBLIC_ERRORS - {"SnektestError"}):
        error_type = assert_isinstance(getattr(snektest, error_name), type)
        assert_true(issubclass(error_type, Exception))
        assert_true(issubclass(error_type, base))

    assertion_failure = assert_isinstance(snektest.AssertionFailure, type)
    assert_true(issubclass(assertion_failure, AssertionError))


@test(mark="fast")
def test_fixture_handles_use_public_scope_representation() -> None:
    """Static decorator values and runtime handles share the public enum."""

    @fixture(scope=Scope.SESSION)
    def scoped_value() -> Generator[int]:
        yield 1

    assert_is(scoped_value().scope, Scope.SESSION)


@test(mark="fast")
def test_internal_invariant_error_is_not_public() -> None:
    """Internal control-flow failures are not part of the supported interface."""
    assert_false(hasattr(snektest, "UnreachableError"))
    assert_false("UnreachableError" in snektest.__all__)
