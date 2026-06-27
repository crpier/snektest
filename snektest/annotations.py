import contextlib
from collections.abc import AsyncGenerator, Callable, Generator
from collections.abc import Coroutine as _Coroutine
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any, Literal, NewType

from pydantic import (
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    TypeAdapter,
    ValidationInfo,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError
from pydantic_core.core_schema import (
    with_info_after_validator_function,
)

type Coroutine[T] = _Coroutine[None, None, T]
type Scope = Literal["function", "session"]


class Fixture[T]:
    """Handle for a sync fixture, produced by calling a `@fixture`-decorated function.

    Pass it to `load_fixture` inside a test to let the runner manage scope and
    teardown, or use it directly as a context manager in standalone scripts:
    `with user_fixture() as user: ...`. In standalone use there is no runner, so
    `scope` is ignored and each `with` block does its own setup/teardown.
    """

    def __init__(
        self, make: Callable[[], Generator[T]], scope: Scope, key: object, name: str
    ) -> None:
        self.make = make
        self.scope = scope
        self.key = key
        self.name = name
        self._gen: Generator[T] | None = None

    def __enter__(self) -> T:
        self._gen = self.make()
        return next(self._gen)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._gen is not None:
            with contextlib.suppress(StopIteration):
                _ = next(self._gen)


class AsyncFixture[T]:
    """Handle for an async fixture, produced by calling a `@fixture`-decorated function.

    Pass it to `load_fixture` (awaited) inside an async test, or use it directly
    as an async context manager in standalone scripts:
    `async with config() as cfg: ...`. In standalone use there is no runner, so
    `scope` is ignored and each block does its own setup/teardown.
    """

    def __init__(
        self,
        make: Callable[[], AsyncGenerator[T]],
        scope: Scope,
        key: object,
        name: str,
    ) -> None:
        self.make = make
        self.scope = scope
        self.key = key
        self.name = name
        self._agen: AsyncGenerator[T] | None = None

    async def __aenter__(self) -> T:
        self._agen = self.make()
        return await anext(self._agen)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._agen is not None:
            with contextlib.suppress(StopAsyncIteration):
                _ = await anext(self._agen)


@dataclass
class PyFileType:
    def __get_pydantic_json_schema__(
        self, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        field_schema = handler(core_schema)
        field_schema.update(format="file-path", type="string")
        return field_schema

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return with_info_after_validator_function(
            self.validate_file,
            handler(source),
        )

    @staticmethod
    def validate_file(path: Path, _: ValidationInfo) -> Path:
        if not path.is_file():
            err_type = "path_not_file"
            msg = "Path does not point to a file"
            raise PydanticCustomError(err_type, msg)
        if path.suffix != ".py":
            err_type = "path_not_python"
            msg = "File path points to is not `.py`"
            raise PydanticCustomError(err_type, msg)
        return path

    def __hash__(self) -> int:
        return hash(self.__class__.__name__)


PyFilePath = Annotated[NewType("PyFile", Path), PyFileType()]
validate_PyFilePath = TypeAdapter[PyFilePath](PyFilePath).validate_python
