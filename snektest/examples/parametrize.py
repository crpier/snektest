"""Param(value=..., name=...) passes one named value per test argument.

Use a tuple value for related inputs and expected output; use separate lists
for separate arguments and their Cartesian product (at most 10,000 cases).
Names are explicit stable selector IDs, never inferred from value reprs.
Param("sqlite") and Param(value="sqlite") both lack a name. For a pair, write
Param(value=(1, 2), name="pair"), not Param(1, 2, name="pair").
Positional construction with both required fields remains supported.

Print the guide with `snektest --agent-docs` and this example with
`snektest --example parametrize`.

Select cases by their nonempty names; `test_cases[]` is an argument error,
even with workers, collect-only, or `--allow-empty`.

Save this example as test_parametrize.py, then try:
    snektest 'test_parametrize.py::test_tuple_value[small]'
    snektest 'test_parametrize.py::test_cartesian_product[hello, world]'
    snektest test_parametrize.py -k 'uppercase or mixed' --collect-only
    snektest test_parametrize.py -k 'fast and not uppercase' --workers 2

`-k` / `--keyword` searches names, bracketed case IDs, markers, and project-relative
file/directory components using case-insensitive substrings. Lowercase `not`,
`and`, `or` and parentheses combine terms. It intersects with `--mark`; an empty
final selection needs `--allow-empty`. Syntax errors precede imports, but matching
still requires collection. Exact selectors remain the precise rerun interface.
"""

lazy from typing import Literal

lazy from snektest import Param, assert_eq, assert_in, test


@test(
    [
        Param(value="hello", name="lowercase"),
        Param(value="WORLD", name="uppercase"),
        Param(value="MiXeD", name="mixed"),
    ],
    mark="fast",
)
def test_string_length(value: str) -> None:
    """Each Param creates one test case with a readable name."""
    assert_eq(len(value), 5)


@test(
    [
        Param(value=(2, 3, 5), name="small"),
        Param(value=(100, 200, 300), name="large"),
    ],
    mark="fast",
)
def test_tuple_value(case: tuple[int, int, int]) -> None:
    """One Param passes one tuple argument, not three separate arguments."""
    left, right, expected = case
    assert_eq(left + right, expected)


@test(
    [Param(value="hello", name="hello"), Param(value="hi", name="hi")],
    [Param(value=" world", name="world"), Param(value=" there", name="there")],
    mark="fast",
)
def test_cartesian_product(greeting: str, target: str) -> None:
    """Multiple parameter lists are combined as a Cartesian product."""
    combined = greeting + target
    assert_eq(combined[: len(greeting)], greeting)


# Give the axis its literal-union type instead of widening values to str.
type Backend = Literal["sqlite", "mariadb"]

BACKENDS: list[Param[Backend]] = [
    Param(value="sqlite", name="sqlite"),
    Param(value="mariadb", name="mariadb"),
]


@test(BACKENDS, mark="fast")
def test_literal_backend(backend: Backend) -> None:
    assert_in(backend, ("sqlite", "mariadb"))
