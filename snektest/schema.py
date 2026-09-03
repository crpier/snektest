"""Optional OpenAPI contract testing powered by Schemathesis."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol, cast

from hypothesis.errors import Unsatisfiable

from snektest.annotations import AsyncFixture, Coroutine, Fixture
from snektest.decorators import (
    Marker,
    _normalize_markers,  # pyright: ignore[reportPrivateUsage]
    _run_hypothesis,  # pyright: ignore[reportPrivateUsage]
    load_fixture,
)
from snektest.models import AssertionFailure, BadRequestError, Param, SnektestError
from snektest.utils import mark_test_function


class SchemaAuthProvider(Protocol):
    """Structural type implemented by native Schemathesis auth providers."""

    def get(self, case: Any, context: Any) -> Any | None:
        """Return authentication data for a generated case."""
        ...

    def set(self, case: Any, data: Any, context: Any) -> None:
        """Apply authentication data to a generated case."""
        ...


type SchemaCheck = Callable[[Any, Any, Any], bool | None]

_MIN_CLIENT_ERROR_STATUS = 400
_MAX_CLIENT_ERROR_STATUS = 500


class SchemaGenerationError(SnektestError):
    """Raised when a selected operation cannot produce the requested cases."""


@dataclass(frozen=True, slots=True)
class SchemaOperationSelector:
    """Match operations whose defined fields all match.

    Multiple selectors in a `SchemaFilter` are alternatives. For example,
    `SchemaOperationSelector(path="/users", method="GET")` selects only that
    method and path combination.
    """

    method: str | None = None
    operation_id: str | None = None
    path: str | None = None
    tag: str | None = None

    def __post_init__(self) -> None:
        if all(
            criterion is None
            for criterion in (self.method, self.operation_id, self.path, self.tag)
        ):
            msg = "SchemaOperationSelector requires at least one matching criterion"
            raise BadRequestError(msg)


@dataclass(frozen=True, slots=True)
class SchemaFilter:
    """Select OpenAPI operations before generating cases or workflows.

    Include selectors are alternatives, exclude selectors always take
    precedence, and `exclude_deprecated` removes deprecated operations.

    ```python
    SchemaFilter(
        exclude=(SchemaOperationSelector(tag="internal"),),
        exclude_deprecated=True,
    )
    ```
    """

    exclude: tuple[SchemaOperationSelector, ...] = ()
    exclude_deprecated: bool = False
    include: tuple[SchemaOperationSelector, ...] = ()


def _schema_config(
    schemathesis: Any,
    request_timeout: float,
    *,
    generation: Literal["positive", "negative"] = "positive",
    expected_statuses: Collection[int] | None = None,
) -> Any:
    """Limit validation to Snektest's documented default contract checks."""
    checks: dict[str, object] = {
        "enabled": False,
        "not_a_server_error": {"enabled": True},
        "response_schema_conformance": {"enabled": True},
    }
    if generation == "negative":
        statuses = (
            tuple(range(_MIN_CLIENT_ERROR_STATUS, _MAX_CLIENT_ERROR_STATUS))
            if expected_statuses is None
            else tuple(expected_statuses)
        )
        if not statuses:
            msg = "negative schema generation requires at least one expected status"
            raise BadRequestError(msg)
        if any(
            status < _MIN_CLIENT_ERROR_STATUS or status >= _MAX_CLIENT_ERROR_STATUS
            for status in statuses
        ):
            msg = "negative schema generation expected statuses must all be 4xx"
            raise BadRequestError(msg)
        checks.update(
            {
                "negative_data_rejection": {
                    "enabled": True,
                    "expected-statuses": statuses,
                },
                "status_code_conformance": {"enabled": True},
            }
        )
    return schemathesis.Config.from_dict(
        {
            "checks": checks,
            "request-timeout": request_timeout,
        }
    )


def _register_auth(schema: Any, auth: type[SchemaAuthProvider] | None) -> None:
    """Scope a native Schemathesis auth provider to this loaded schema."""
    if auth is not None:
        _ = schema.auth()(auth)


def _apply_operation_filter(schema: Any, operation_filter: SchemaFilter | None) -> Any:
    """Apply selectors before collection so workflow links use the same subset."""
    if operation_filter is None:
        return schema
    for selector in operation_filter.include:
        schema = schema.include(
            method=selector.method.upper() if selector.method is not None else None,
            operation_id=selector.operation_id,
            path=selector.path,
            tag=selector.tag,
        )
    for selector in operation_filter.exclude:
        schema = schema.exclude(
            method=selector.method.upper() if selector.method is not None else None,
            operation_id=selector.operation_id,
            path=selector.path,
            tag=selector.tag,
        )
    if operation_filter.exclude_deprecated:
        schema = schema.exclude(deprecated=True)
    return schema


def _load_optional_module(
    module_name: str,
    *,
    importer: Callable[[str], ModuleType] = import_module,
) -> ModuleType:
    """Load an optional schema dependency with an actionable installation error."""
    try:
        return importer(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        msg = "test_schema requires the optional schema dependencies; install `snektest[schema]`"
        raise BadRequestError(msg) from exc


def _collect_operations(schema: Any) -> list[Any]:
    """Collect through Schemathesis's filter-aware result iterator."""
    result_module = _load_optional_module("schemathesis.core.result")
    collected_operations: list[Any] = []
    for operation_result in schema.get_all_operations():
        if isinstance(operation_result, result_module.Ok):
            collected_operations.append(operation_result.ok())
        else:
            raise operation_result.err()
    return collected_operations


async def _resolve_runtime_value[T](
    value: T | Fixture[T] | AsyncFixture[T],
) -> T:
    """Resolve a literal or fixture-backed decorator argument on the test loop."""
    if isinstance(value, AsyncFixture):
        return await load_fixture(value)
    if isinstance(value, Fixture):
        return load_fixture(cast("Fixture[T]", value))
    return value


def test_schema(  # noqa: PLR0913
    schema_path: str | Path,
    *,
    base_url: str | Fixture[str] | AsyncFixture[str],
    headers: dict[str, str]
    | Fixture[dict[str, str]]
    | AsyncFixture[dict[str, str]]
    | None = None,
    auth: type[SchemaAuthProvider] | None = None,
    checks: Sequence[SchemaCheck] = (),
    generation: Literal["positive", "negative"] = "positive",
    expected_statuses: Collection[int] | None = None,
    operations: SchemaFilter | None = None,
    request_timeout: float = 10.0,
    mark: Marker | None = None,
) -> Callable[
    [Callable[[], Coroutine[None] | None]],
    Callable[[], Coroutine[None]],
]:
    """Generate one OpenAPI contract test per operation.

    The decorated function supplies the test name and Hypothesis settings; its
    body is not called. `base_url` and `headers` may be literals or fixture
    handles. Native Schemathesis auth providers and checks extend request
    authentication and response validation. Negative generation requires an
    allowed, documented 4xx response for each schema-violating request.
    """
    schemathesis = _load_optional_module("schemathesis")
    failures_module = _load_optional_module("schemathesis.core.failures")
    failure_group_type = cast("type[BaseException]", failures_module.FailureGroup)
    schema = schemathesis.openapi.from_path(
        schema_path,
        config=_schema_config(
            schemathesis,
            request_timeout,
            generation=generation,
            expected_statuses=expected_statuses,
        ),
    )
    schema = _apply_operation_filter(schema, operations)
    _register_auth(schema, auth)
    additional_checks = list(checks)
    collected_operations = _collect_operations(schema)
    if not collected_operations:
        if operations is None:
            msg = f"OpenAPI schema `{schema_path}` contains no operations"
        else:
            msg = (
                f"Operation filter selected no operations from OpenAPI schema "
                f"`{schema_path}`"
            )
        raise BadRequestError(msg)

    markers = _normalize_markers(mark)
    generation_mode = schemathesis.GenerationMode(generation)
    operation_params = [
        Param(
            value=operation,
            name=(
                cast("str", operation.label)
                if generation == "positive"
                else f"negative {operation.label}"
            ),
        )
        for operation in collected_operations
    ]

    def decorator(
        test_func: Callable[[], Coroutine[None] | None],
    ) -> Callable[[], Coroutine[None]]:
        @wraps(test_func)
        async def wrapper(operation: Any) -> None:
            resolved_base_url = await _resolve_runtime_value(base_url)
            resolved_headers = (
                None if headers is None else await _resolve_runtime_value(headers)
            )

            def run_one_example(case: Any) -> None:
                try:
                    _ = case.call_and_validate(
                        base_url=resolved_base_url,
                        headers=resolved_headers,
                        additional_checks=additional_checks,
                    )
                except failure_group_type as exc:
                    raise AssertionFailure(str(exc)) from None

            def run_hypothesis() -> None:
                try:
                    _run_hypothesis(
                        wrapper,
                        (operation.as_strategy(generation_mode=generation_mode),),
                        run_one_example,
                    )
                except Unsatisfiable as exc:
                    if generation != "negative":
                        raise
                    msg = (
                        f"Could not generate schema-violating requests for "
                        f"`{operation.label}`. The operation may have no negatable "
                        "request constraints; exclude it with `SchemaFilter`."
                    )
                    raise SchemaGenerationError(msg) from exc

            await asyncio.to_thread(run_hypothesis)

        mark_test_function(wrapper, (operation_params,), markers)
        return cast("Callable[[], Coroutine[None]]", wrapper)

    return decorator


def test_schema_workflow(  # noqa: C901, PLR0913
    schema_path: str | Path,
    *,
    base_url: str | Fixture[str] | AsyncFixture[str],
    headers: dict[str, str]
    | Fixture[dict[str, str]]
    | AsyncFixture[dict[str, str]]
    | None = None,
    auth: type[SchemaAuthProvider] | None = None,
    checks: Sequence[SchemaCheck] = (),
    operations: SchemaFilter | None = None,
    request_timeout: float = 10.0,
    mark: Marker | None = None,
) -> Callable[
    [Callable[[], Coroutine[None] | None]],
    Callable[[], Coroutine[None]],
]:
    """Generate linked OpenAPI operation sequences as one stateful test.

    Schemathesis discovers explicit OpenAPI links and inferred producer-consumer
    relationships. Hypothesis settings on the decorated function control the
    number and length of generated workflows; the function body is not called.
    """
    schemathesis = _load_optional_module("schemathesis")
    failures_module = _load_optional_module("schemathesis.core.failures")
    errors_module = _load_optional_module("schemathesis.core.errors")
    failure_group_type = cast("type[BaseException]", failures_module.FailureGroup)
    invalid_state_machine_type = cast(
        "type[BaseException]",
        errors_module.InvalidStateMachine,
    )
    schema = schemathesis.openapi.from_path(
        schema_path,
        config=_schema_config(schemathesis, request_timeout),
    )
    schema = _apply_operation_filter(schema, operations)
    _register_auth(schema, auth)
    try:
        base_state_machine = schema.as_state_machine()
    except invalid_state_machine_type as exc:
        msg = f"Invalid OpenAPI workflow links in `{schema_path}`:\n\n{exc}"
        raise BadRequestError(msg) from exc
    if not base_state_machine.bundles:
        if operations is None:
            msg = (
                f"OpenAPI schema `{schema_path}` contains no usable workflow links. "
                "Define OpenAPI links between producer and consumer operations, or add "
                "response and parameter schemas from which Schemathesis can infer them."
            )
        else:
            msg = (
                f"Operation filter left no usable workflow links in OpenAPI schema "
                f"`{schema_path}`. Keep both the producer and consumer operation for "
                "at least one link."
            )
        raise BadRequestError(msg)
    additional_checks = list(checks)
    markers = _normalize_markers(mark)

    def decorator(
        test_func: Callable[[], Coroutine[None] | None],
    ) -> Callable[[], Coroutine[None]]:
        @wraps(test_func)
        async def wrapper() -> None:
            resolved_base_url = await _resolve_runtime_value(base_url)
            resolved_headers = (
                None if headers is None else await _resolve_runtime_value(headers)
            )
            workflow_steps: list[str] = []

            class Workflow(base_state_machine):
                def setup(self) -> None:
                    workflow_steps.clear()

                def call(self, case: Any, **kwargs: Any) -> Any:
                    workflow_steps.append(
                        f"{case.method.upper()} {case.formatted_path}"
                    )
                    response = case.call(**kwargs)
                    workflow_steps[-1] += f" -> {response.status_code}"
                    return response

                def get_call_kwargs(self, _case: Any) -> dict[str, Any]:
                    return {
                        "base_url": resolved_base_url,
                        "headers": resolved_headers,
                    }

                def validate_response(
                    self,
                    response: Any,
                    case: Any,
                    _additional_checks: list[SchemaCheck] | None = None,
                    **kwargs: Any,
                ) -> None:
                    case.validate_response(
                        response,
                        additional_checks=additional_checks,
                        transport_kwargs=kwargs,
                    )

            hypothesis_settings = getattr(
                cast("Any", wrapper),
                "_hypothesis_internal_use_settings",
                None,
            )
            try:
                await asyncio.to_thread(
                    Workflow.run,
                    settings=hypothesis_settings,
                )
            except failure_group_type as exc:
                rendered_steps = "\n".join(
                    f"{index}. {step}"
                    for index, step in enumerate(workflow_steps, start=1)
                )
                message = f"{exc}\n\nMinimized workflow:\n{rendered_steps}"
                raise AssertionFailure(message) from None

        mark_test_function(wrapper, (), markers)
        return cast("Callable[[], Coroutine[None]]", wrapper)

    return decorator
