"""Supported top-level interface for authoring and integrating snektest tests."""

from snektest._version import __version__ as __version__
from snektest.annotations import (
    AsyncFixture,
    Fixture,
    Scope,
)
from snektest.assertions import (
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
from snektest.benchmark import assert_benchmark
from snektest.decorators import (
    Marker,
    fixture,
    load_fixture,
    test,
    test_hypothesis,
)
from snektest.models import AssertionFailure as AssertionFailure
from snektest.models import BadRequestError as BadRequestError
from snektest.models import CollectionError as CollectionError
from snektest.models import FixtureError as FixtureError
from snektest.models import Param as Param
from snektest.models import SnektestError as SnektestError
from snektest.models import TestTimeoutError as TestTimeoutError
from snektest.schema import (
    SchemaAuthProvider,
    SchemaCheck,
    SchemaFilter,
    SchemaGenerationError,
    SchemaOperationSelector,
    test_schema,
    test_schema_workflow,
)

__all__ = [
    "AssertionFailure",
    "AsyncFixture",
    "BadRequestError",
    "CollectionError",
    "Fixture",
    "FixtureError",
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
    "fail",
    "fixture",
    "load_fixture",
    "test",
    "test_hypothesis",
    "test_schema",
    "test_schema_workflow",
]
