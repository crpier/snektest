"""Parameter lists form Cartesian products capped at 10,000 cases per test.

Select cases by their nonempty names; `test_cases[]` is an argument error,
even with workers, collect-only, or `--allow-empty`.

Save this example as test_parametrize.py, then try:
    snektest test_parametrize.py -k 'uppercase or mixed' --collect-only
    snektest test_parametrize.py -k 'fast and not uppercase' --workers 2

`-k` / `--keyword` searches names, bracketed case IDs, markers, and project-relative
file/directory components using case-insensitive substrings. Lowercase `not`,
`and`, `or` and parentheses combine terms. It intersects with `--mark`; an empty
final selection needs `--allow-empty`. Syntax errors precede imports, but matching
still requires collection. Exact selectors remain the precise rerun interface.
"""

from snektest import Param, assert_eq, test


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
    [Param(value="hello", name="hello"), Param(value="hi", name="hi")],
    [Param(value=" world", name="world"), Param(value=" there", name="there")],
    mark="fast",
)
def test_cartesian_product(greeting: str, target: str) -> None:
    """Multiple parameter lists are combined as a Cartesian product."""
    combined = greeting + target
    assert_eq(combined[: len(greeting)], greeting)
