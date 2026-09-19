"""Compile pytest-style keyword expressions without evaluating Python code."""

from collections.abc import Iterable
from re import compile  # noqa: A004 - this module only compiles regular expressions

from snektest.models import BadRequestError

_WORD = compile(r"[\w:+.\[\]\\/\-]+")
_PRECEDENCE = {"or": 1, "and": 2, "not": 3}


class KeywordExpression:
    """A validated expression evaluated against separate searchable names.

    Operators are lowercase; name matching is case-insensitive. An empty
    expression selects everything, as in pytest. Compilation and evaluation
    are iterative so deeply nested user input cannot exhaust the call stack.
    """

    def __init__(self, expression: str) -> None:  # noqa: C901
        expression = expression.lstrip()
        self._instructions: list[str] = []
        operators: list[str] = []
        expects_operand = True
        position = 0
        while position < len(expression):
            character = expression[position]
            if character in " \t":
                position += 1
                continue
            match = _WORD.match(expression, position)
            term = match.group() if match else character
            column = position + 1
            position += len(term)
            if (term == "(" and expects_operand) or (term == "not" and expects_operand):
                operators.append(term)
            elif term in {"and", "or"} and not expects_operand:
                while (
                    operators
                    and operators[-1] != "("
                    and (_PRECEDENCE[operators[-1]] >= _PRECEDENCE[term])
                ):
                    self._instructions.append(operators.pop())
                operators.append(term)
                expects_operand = True
            elif term == ")" and not expects_operand and "(" in operators:
                while operators[-1] != "(":
                    self._instructions.append(operators.pop())
                operators.pop()
            elif match and term not in _PRECEDENCE and expects_operand:
                self._instructions.append(f"name:{term.lower()}")
                expects_operand = False
            else:
                message = f"Invalid keyword expression at column {column}: unexpected {term!r}"
                raise BadRequestError(message)
        if expects_operand and (operators or self._instructions):
            message = "Invalid keyword expression: expected a name or '(' at end"
            raise BadRequestError(message)
        if "(" in operators:
            message = "Invalid keyword expression: missing ')'"
            raise BadRequestError(message)
        self._instructions.extend(reversed(operators))

    def matches(self, names: Iterable[str]) -> bool:
        """Match substrings within names, never across component boundaries."""
        lowered_names = tuple(name.lower() for name in names)
        values: list[bool] = []
        for instruction in self._instructions:
            if instruction == "not":
                values.append(not values.pop())
            elif instruction in {"and", "or"}:
                right = values.pop()
                left = values.pop()
                values.append(left and right if instruction == "and" else left or right)
            else:
                word = instruction.removeprefix("name:")
                values.append(any(word in name for name in lowered_names))
        return values[0] if values else True
