"""Property-based tests for fixture teardown ordering.

The teardown contract is stateful and was the source of a real bug (function
fixtures must tear down first-in-last-out, and a fixture must be torn down
*before* any fixture it set up as a dependency). These properties drive a
`FixtureRegistry` directly with generated fixture topologies and assert the
ordering holds for every shape.
"""

from __future__ import annotations

from collections.abc import Generator

from hypothesis import settings
from hypothesis import strategies as st

from snektest import Fixture, assert_eq
from snektest.decorators import test_hypothesis
from snektest.fixtures import FixtureRegistry


def _recording_fixture(
    label: int, events: list[tuple[str, int]]
) -> Fixture[int]:
    """A function fixture that records its own setup and teardown."""

    def gen() -> Generator[int]:
        events.append(("setup", label))
        yield label
        events.append(("teardown", label))

    return Fixture(make=gen, scope="function", key=object(), name=f"f{label}")


def _nested_fixture(
    level: int, max_level: int, events: list[tuple[str, int]], registry: FixtureRegistry
) -> Fixture[int]:
    """A fixture that, during its own setup, loads the next level as a dependency."""

    def gen() -> Generator[int]:
        events.append(("setup", level))
        if level + 1 < max_level:
            _ = registry.load_function(
                _nested_fixture(level + 1, max_level, events, registry)
            )
        yield level
        events.append(("teardown", level))

    return Fixture(make=gen, scope="function", key=object(), name=f"f{level}")


@settings(deadline=None)
@test_hypothesis(st.integers(min_value=0, max_value=20), mark="fast")
async def test_independent_fixtures_tear_down_last_in_first_out(count: int) -> None:
    """Independent function fixtures tear down in reverse setup order."""
    registry = FixtureRegistry()
    events: list[tuple[str, int]] = []
    labels = list(range(count))

    for label in labels:
        _ = registry.load_function(_recording_fixture(label, events))

    failures = await registry.teardown_function_fixtures()

    assert_eq(failures, [])
    setups = [label for kind, label in events if kind == "setup"]
    teardowns = [label for kind, label in events if kind == "teardown"]
    assert_eq(setups, labels)
    assert_eq(teardowns, list(reversed(labels)))


@settings(deadline=None)
@test_hypothesis(st.integers(min_value=0, max_value=20), mark="fast")
async def test_dependency_torn_down_after_its_dependent(depth: int) -> None:
    """A fixture loaded as a dependency tears down after the fixture that loaded it."""
    registry = FixtureRegistry()
    events: list[tuple[str, int]] = []

    if depth > 0:
        _ = registry.load_function(_nested_fixture(0, depth, events, registry))

    failures = await registry.teardown_function_fixtures()

    assert_eq(failures, [])
    # Outermost (level 0) sets up first and, being registered last on the stack,
    # tears down first; each level tears down before the dependency it loaded.
    setups = [label for kind, label in events if kind == "setup"]
    teardowns = [label for kind, label in events if kind == "teardown"]
    assert_eq(setups, list(range(depth)))
    assert_eq(teardowns, list(range(depth)))
