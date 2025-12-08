from typing import Any

from snektest.models import AssertionFailure
from snektest.presenter import render_assertion_failure


def assert_eq(actual: Any, expected: Any, *, msg: str | None = None) -> None:
    if actual != expected:
        message = msg or "assert_eq failed"
        raise AssertionFailure(
            message,
            actual=actual,
            expected=expected,
        )


# try:
#     assert_eq(5, 10)
# except AssertionFailure as exc:
#     render_assertion_failure(exc)
