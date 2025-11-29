from collections.abc import Coroutine as _Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NewType

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
