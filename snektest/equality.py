"""Bounded, failure-only explanations for the public equality assertion.

Only exact builtins are inspected: diagnostics never invoke user repr, equality,
iteration, or indexing hooks. The assertion itself still owns the original !=.
"""

lazy from dataclasses import dataclass
lazy from difflib import unified_diff
lazy from itertools import islice
lazy from typing import cast

_MAX_TEXT = 8_000
_MAX_STRING_SCAN = 100_000
_MAX_ITEMS = 50
_MAX_DEPTH = 5
_MAX_NODES = 1_000
_MAX_PREVIEW_NODES = 100
_MAX_PREVIEW_ITEMS = 6
_MAX_PREVIEW_TEXT = 160
_MAX_INT_BITS = 2_048
_MAX_KEY_TEXT = 256
_MAX_DIFF_LINES = 80
_MAX_INPUT_LINES = 200
_STRING_CONTEXT = 40
_SCALARS = (str, bytes, int, float, bool, type(None))


class _InspectionStoppedError(Exception):
    """An explanation cannot safely inspect the rest of a comparison."""


@dataclass
class _Budget:
    nodes: int = _MAX_NODES
    preview_nodes: int = _MAX_PREVIEW_NODES


def _clip(text: str, limit: int = _MAX_TEXT) -> str:
    marker = "... [diagnostic truncated]"
    return text if len(text) <= limit else text[: limit - len(marker)] + marker


def _is_scalar(value: object) -> bool:
    return any(type(value) is kind for kind in _SCALARS)


def _is_container(value: object) -> bool:
    return type(value) is list or type(value) is tuple or type(value) is dict


def _opaque(value: object) -> str:
    kind = type(value)
    # A custom metaclass may override even class-name access.
    name = kind.__name__ if type(kind) is type else "object"
    return f"<{name[:80]}>" if type(name) is str else "<object>"


def _preview(  # noqa: C901, PLR0911
    value: object, budget: _Budget, depth: int = 0, seen: frozenset[int] = frozenset()
) -> str:
    budget.preview_nodes -= 1
    if budget.preview_nodes < 0 or depth >= _MAX_DEPTH:
        return "..."
    if type(value) is str or type(value) is bytes:
        text = value
        rendered = repr(text[:_MAX_PREVIEW_TEXT])
        return rendered + ("..." if len(text) > _MAX_PREVIEW_TEXT else "")
    if type(value) is int and value.bit_length() > _MAX_INT_BITS:
        return f"<int: {value.bit_length()} bits>"
    if _is_scalar(value):
        return repr(value)
    if id(value) in seen:
        return "<cycle>"
    nested_seen = seen | {id(value)}
    if type(value) is list or type(value) is tuple:
        sequence = cast("list[object] | tuple[object, ...]", value)
        parts = [
            _preview(item, budget, depth + 1, nested_seen)
            for item in islice(sequence, _MAX_PREVIEW_ITEMS)
        ]
        if len(sequence) > _MAX_PREVIEW_ITEMS:
            parts.append("...")
        if type(value) is tuple:
            return "(" + ", ".join(parts) + ("," if len(sequence) == 1 else "") + ")"
        return "[" + ", ".join(parts) + "]"
    if type(value) is dict:
        mapping = cast("dict[object, object]", value)
        parts = [
            f"{_preview(key, budget, depth + 1, nested_seen)}: {_preview(item, budget, depth + 1, nested_seen)}"
            for key, item in islice(mapping.items(), _MAX_PREVIEW_ITEMS)
        ]
        if len(mapping) > _MAX_PREVIEW_ITEMS:
            parts.append("...")
        return "{" + ", ".join(parts) + "}"
    return _opaque(value)


def _string_index(actual: str, expected: str) -> int | None:
    limit = min(len(actual), len(expected), _MAX_STRING_SCAN)
    index = 0
    while index < limit and actual[index] == expected[index]:
        index += 1
    if index < limit:
        return index
    if min(len(actual), len(expected)) > _MAX_STRING_SCAN:
        msg = "Comparison truncated after 100000 characters."
        raise _InspectionStoppedError(msg)
    return None if len(actual) == len(expected) else index


def _string_details(actual: str, expected: str, index: int) -> str:
    start = max(index - _STRING_CONTEXT, 0)
    end = index + _STRING_CONTEXT + 1
    return (
        f"First difference at character {index} (0-based):\n"
        f"  actual[{start}:{end}]: {actual[start:end]!r}\n"
        f"  expected[{start}:{end}]: {expected[start:end]!r}"
    )


def _multiline_details(actual: str, expected: str, index: int) -> str:
    actual_lines = [
        line[:_MAX_KEY_TEXT]
        for line in actual[:_MAX_STRING_SCAN].splitlines()[:_MAX_INPUT_LINES]
    ]
    expected_lines = [
        line[:_MAX_KEY_TEXT]
        for line in expected[:_MAX_STRING_SCAN].splitlines()[:_MAX_INPUT_LINES]
    ]
    lines = list(
        islice(
            unified_diff(
                expected_lines,
                actual_lines,
                fromfile="expected",
                tofile="actual",
                n=2,
                lineterm="",
            ),
            _MAX_DIFF_LINES + 1,
        )
    )
    truncated = (
        len(lines) > _MAX_DIFF_LINES
        or len(actual) > _MAX_STRING_SCAN
        or len(expected) > _MAX_STRING_SCAN
        or actual.count("\n", 0, _MAX_STRING_SCAN) >= _MAX_INPUT_LINES
        or expected.count("\n", 0, _MAX_STRING_SCAN) >= _MAX_INPUT_LINES
        or any(
            len(line) > _MAX_KEY_TEXT
            for line in actual[:_MAX_STRING_SCAN].splitlines()[:_MAX_INPUT_LINES]
        )
        or any(
            len(line) > _MAX_KEY_TEXT
            for line in expected[:_MAX_STRING_SCAN].splitlines()[:_MAX_INPUT_LINES]
        )
    )
    result = (
        "\n".join(lines[:_MAX_DIFF_LINES])
        if lines
        else _string_details(actual, expected, index)
    )
    return result + (
        "\nComparison truncated: multiline diff limits reached." if truncated else ""
    )


def _key_is_safe(key: object) -> bool:
    if not _is_scalar(key):
        return False
    if type(key) is int:
        return key.bit_length() <= _MAX_INT_BITS
    if type(key) is str or type(key) is bytes:
        return len(key) <= _MAX_KEY_TEXT
    return True


def _dict_details(
    actual: dict[object, object],
    expected: dict[object, object],
    path: str,
    depth: int,
    budget: _Budget,
) -> list[str]:
    # Inspect all keys before any lookup: a colliding custom key could invoke
    # equality even when the requested key itself is a safe builtin.
    if max(len(actual), len(expected)) > _MAX_ITEMS:
        msg = f"Comparison truncated at {path}: dictionary exceeds 50 items."
        raise _InspectionStoppedError(msg)
    if not all(_key_is_safe(key) for key in actual) or not all(
        _key_is_safe(key) for key in expected
    ):
        msg = f"Details unavailable at {path}: custom or oversized dictionary keys are not inspected."
        raise _InspectionStoppedError(msg)
    missing = [key for key in expected if key not in actual]
    extra = [key for key in actual if key not in expected]
    differing: list[object] = []
    children: list[str] = []
    for key, expected_value in expected.items():
        if key in actual:
            nested = _details(
                actual[key],
                expected_value,
                f"{path}[{_preview(key, budget)}]",
                depth + 1,
                budget,
            )
            if nested:
                differing.append(key)
                children.extend(nested)
    lines: list[str] = []
    location = "" if path == "$" else f" at {path}"
    if missing:
        lines.append(
            f"Missing keys (expected only){location}: {_preview(missing, budget)}"
        )
    if extra:
        lines.append(f"Extra keys (actual only){location}: {_preview(extra, budget)}")
    if differing:
        lines.append(f"Differing keys{location}: {_preview(differing, budget)}")
    return lines + children


def _sequence_details(
    actual: list[object] | tuple[object, ...],
    expected: list[object] | tuple[object, ...],
    path: str,
    depth: int,
    budget: _Budget,
) -> list[str]:
    for index, (left, right) in enumerate(
        islice(zip(actual, expected, strict=False), _MAX_ITEMS)
    ):
        nested = _details(left, right, f"{path}[{index}]", depth + 1, budget)
        if nested:
            return [f"First differing index at {path}: {index}", *nested]
    if min(len(actual), len(expected)) > _MAX_ITEMS:
        msg = f"Comparison truncated at {path}: sequence scan exceeds 50 items."
        raise _InspectionStoppedError(msg)
    if len(actual) != len(expected):
        index = min(len(actual), len(expected))
        return [
            f"First differing index at {path}: {index} (length mismatch: actual {len(actual)}, expected {len(expected)})"
        ]
    return []


def _details(  # noqa: C901, PLR0911, PLR0912
    actual: object, expected: object, path: str, depth: int, budget: _Budget
) -> list[str]:
    budget.nodes -= 1
    if budget.nodes < 0 or depth >= _MAX_DEPTH:
        msg = f"Comparison truncated at {path}: node or depth limit reached."
        raise _InspectionStoppedError(msg)
    if actual is expected:
        return []
    if type(actual) is dict and type(expected) is dict:
        return _dict_details(
            cast("dict[object, object]", actual),
            cast("dict[object, object]", expected),
            path,
            depth,
            budget,
        )
    if (type(actual) is list and type(expected) is list) or (
        type(actual) is tuple and type(expected) is tuple
    ):
        return _sequence_details(
            cast("list[object] | tuple[object, ...]", actual),
            cast("list[object] | tuple[object, ...]", expected),
            path,
            depth,
            budget,
        )
    if type(actual) is str and type(expected) is str:
        index = _string_index(actual, expected)
        if index is None:
            return []
        if max(len(actual), len(expected)) > _MAX_PREVIEW_TEXT:
            return [f"At {path}:", _string_details(actual, expected, index)]
    elif _is_scalar(actual) and _is_scalar(expected):
        if type(actual) is int and actual.bit_length() > _MAX_INT_BITS:
            msg = f"Details unavailable at {path}: oversized integer."
            raise _InspectionStoppedError(msg)
        if type(expected) is int and expected.bit_length() > _MAX_INT_BITS:
            msg = f"Details unavailable at {path}: oversized integer."
            raise _InspectionStoppedError(msg)
        if (
            type(actual) is bytes
            and type(expected) is bytes
            and max(len(actual), len(expected)) > _MAX_STRING_SCAN
        ):
            msg = f"Comparison truncated at {path}: oversized bytes."
            raise _InspectionStoppedError(msg)
        if actual == expected:
            return []
    elif (
        (_is_scalar(actual) or _is_container(actual))
        and (_is_scalar(expected) or _is_container(expected))
        and type(actual) is not type(expected)
    ):
        return [
            f"At {path}: {_preview(actual, budget)} != {_preview(expected, budget)} (types differ)"
        ]
    else:
        msg = f"Details unavailable at {path}: custom values are not inspected."
        raise _InspectionStoppedError(msg)
    return [f"At {path}: {_preview(actual, budget)} != {_preview(expected, budget)}"]


def _describe_inequality(actual: object, expected: object) -> str:
    """Explain an already-established mismatch without invoking user hooks."""
    budget = _Budget()
    summary = f"{_clip(_preview(actual, budget), 640)} != {_clip(_preview(expected, budget), 640)}"
    try:
        if type(actual) is str and type(expected) is str:
            index = _string_index(actual, expected)
            if index is not None:
                if (
                    "\n" in actual[:_MAX_STRING_SCAN]
                    or "\n" in expected[:_MAX_STRING_SCAN]
                ):
                    return _clip(
                        "Multiline strings differ\n"
                        + _multiline_details(actual, expected, index)
                    )
                if max(len(actual), len(expected)) > _MAX_PREVIEW_TEXT:
                    return (
                        f"Strings differ (actual length {len(actual)}, expected length {len(expected)})\n"
                        + _string_details(actual, expected, index)
                    )
        if type(actual) is dict or type(actual) is list or type(actual) is tuple:
            summary += "\n" + "\n".join(_details(actual, expected, "$", 0, budget))
    except _InspectionStoppedError as stopped:
        summary += "\n" + str(stopped)
    return _clip(summary)


def describe_inequality(actual: object, expected: object) -> str:
    """Keep unexpected diagnostic errors from replacing an established failure."""
    try:
        return _describe_inequality(actual, expected)
    except Exception:
        return "Equality mismatch (diagnostics unavailable)."
