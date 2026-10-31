from contextlib import AbstractContextManager
from pathlib import Path
from typing import no_type_check as _no_type_check

test_typing = _no_type_check

def assert_type_error(rule: str, /) -> AbstractContextManager[None]: ...

# Internal runner scope; not a top-level public interface.
def typing_configuration(path: Path | None) -> AbstractContextManager[None]: ...
