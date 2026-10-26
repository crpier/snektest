"""Supported top-level interface for authoring and integrating snektest tests."""

lazy from snektest._version import __version__ as __version__
lazy from snektest.annotations import (
    AsyncFixture,
    Fixture,
    Scope,
)
lazy from snektest.assertions import (
    assert_eq,
    assert_false,
    assert_ge,
    assert_gt,
    assert_in,
    assert_is,
    assert_is_none,
    assert_is_not,
    assert_is_not_none,
    assert_isinstance,
    assert_le,
    assert_len,
    assert_lt,
    assert_memory,
    assert_ne,
    assert_not_in,
    assert_not_isinstance,
    assert_raises,
    assert_true,
    fail,
)
lazy from snektest.benchmark import assert_benchmark
lazy from snektest.decorators import (
    Marker,
    fixture,
    load_fixture,
    skip,
    test,
    test_hypothesis,
    xfail,
)
lazy from snektest.log_capture import LogCapture, capture_logs
lazy from snektest.models import AssertionFailure as AssertionFailure
lazy from snektest.models import BadRequestError as BadRequestError
lazy from snektest.models import CollectionError as CollectionError
lazy from snektest.models import FixtureError as FixtureError
lazy from snektest.models import Param as Param
lazy from snektest.models import SnektestError as SnektestError
lazy from snektest.models import TestTimeoutError as TestTimeoutError
lazy from snektest.schema import (
    GraphQLFilter,
    GraphQLOperationSelector,
    SchemaAuthProvider,
    SchemaCheck,
    SchemaFilter,
    SchemaGenerationError,
    SchemaOperationSelector,
    test_graphql,
    test_schema,
    test_schema_workflow,
)
lazy from snektest.type_contracts import assert_type_error, test_typing

__all__ = [
    "AssertionFailure",
    "AsyncFixture",
    "BadRequestError",
    "CollectionError",
    "Fixture",
    "FixtureError",
    "GraphQLFilter",
    "GraphQLOperationSelector",
    "LogCapture",
    "Marker",
    "Param",
    "SchemaAuthProvider",
    "SchemaCheck",
    "SchemaFilter",
    "SchemaGenerationError",
    "SchemaOperationSelector",
    "Scope",
    "SnektestError",
    "TestTimeoutError",
    "__version__",
    "assert_benchmark",
    "assert_eq",
    "assert_false",
    "assert_ge",
    "assert_gt",
    "assert_in",
    "assert_is",
    "assert_is_none",
    "assert_is_not",
    "assert_is_not_none",
    "assert_isinstance",
    "assert_le",
    "assert_len",
    "assert_lt",
    "assert_memory",
    "assert_ne",
    "assert_not_in",
    "assert_not_isinstance",
    "assert_raises",
    "assert_true",
    "assert_type_error",
    "capture_logs",
    "fail",
    "fixture",
    "load_fixture",
    "skip",
    "test",
    "test_graphql",
    "test_hypothesis",
    "test_schema",
    "test_schema_workflow",
    "test_typing",
    "xfail",
]
