"""Basic tests for `--collect-only`, `--fail-fast`, and `--durations` workflows.

Requires Python 3.15 or newer; Python 3.14 is no longer supported.

The standalone CLI enables context-aware warnings before collection and workers.
Opt out with `python -X context_aware_warnings=0 -m snektest test_basic.py`, or
PYTHON_CONTEXT_AWARE_WARNINGS=0. Explicit -X wins over the environment; -E/-I
ignore the environment. Default startup may relaunch the same interpreter/venv
and repeat site hooks. Already-enabled/explicitly opted-out startup is unchanged;
library imports and programmatic runners never restart or change host policy.

POSIX CLI startup preserves ignored SIGINT/SIGQUIT/SIGHUP, including workers and
application subprocesses. Shell background jobs commonly ignore SIGINT/SIGQUIT;
nohup concerns SIGHUP. Application signal tests should set their child's policy
explicitly before exec, not reset runner-global handlers. Caught handlers do not
survive exec/spawn. Normal SIGINT exits 2 with interruption JSON if shutdown
completes; worker coordinator interruption can terminate children without fixture
teardown. SIGKILL/hard interruption need not clean up or emit final JSON. There
is no arbitrary process-tree containment or signal-reset flag. See
docs/cli-startup.md for the tested SIGINT-only background launcher recipe.

Observe live activity without mixing progress into stdout JSON:
    snektest --json-output --progress-output progress-001.jsonl test_basic.py >results.json
Then, in another terminal: tail -f progress-001.jsonl
Use a new sidecar path each run; --progress-interval defaults to 5 seconds
(finite, minimum 0.05). Collection/file, test/occurrence/attempt, fixture phases,
and worker incarnation are version-1 metadata events, not captured user output.
Missing run_finished means incomplete observation. Worker crashes/interruption
can leave unmatched child spans even with a terminal event; cleanup is not proven.
Text/snapshots and output are bounded with explicit clipping/truncation markers;
a companion .lock file serializes writes. No locals, exception messages, or stacks
are captured. The observer thread is not a hard deadline/deadlock detector;
an event-loop heartbeat cannot run on a synchronously blocked loop, and the thread
can also stall on GIL/native/IO work. Keep an outer timeout. SIGKILL/hard interruption
may prevent final JSON/events or cleanup; arbitrary subprocess trees are not
contained. See docs/progress.md for full semantics, privacy and platform limits.
Existing completion reporters and stdout JSON schema remain unchanged.

Pass observed values first and expected values second in comparisons. Bind the
return values of narrowing helpers; use `_ =` when only checking a value's type.
For exception text, `match=` is regex search, capture plus assert_eq is exact,
and `msg=` is only the failure diagnostic. Inspect original causes explicitly.
Print the usage guide with `snektest --agent-docs`.

Directory discovery applies Git ignore rules to symlink names, not targets.
Explicit file selection bypasses ignores; unexpected Git filtering failures in a
worktree are collection errors.

Collection failures identify the selected file and original error with user source,
syntax carets, causes, and groups. Use `snektest --full-traceback test_basic.py`
for bounded framework/dependency details. The flag leaves JSON diagnostics and
collection semantics unchanged; `--json-output` retains captured import output.

Warning message formatting failures use safe fallback text; capture still restores
standard streams and debugger hooks.
"""

lazy import re

lazy from snektest import (
    AssertionFailure,
    SnektestError,
    assert_eq,
    assert_in,
    assert_is_not_none,
    assert_isinstance,
    assert_raises,
    fail,
    test,
)


@test(mark="fast")
def test_addition() -> None:
    """Use rich assertions in a synchronous test."""
    assert_eq(1 + 1, 2)


@test(mark="fast")
def test_string_membership() -> None:
    """Use assertion helpers instead of bare assert statements."""
    assert_in("snek", "snektest")


@test(mark="fast")
def test_public_error_base() -> None:
    """Catch user-facing framework failures under one conventional base."""
    with assert_raises(SnektestError, match="expected failure"):
        fail("expected failure")


@test(mark="fast")
def test_exception_message_match() -> None:
    """Search exception text with a string or compiled regex; msg is an override.

    Invalid regexes/bytes patterns raise BadRequestError before the body.
    Mismatches and unrenderable messages fail without matching fallback text.
    """
    pattern = re.compile(r"^invalid field: name$", re.IGNORECASE)
    with assert_raises(
        ValueError, match=pattern, msg="expected a field error"
    ) as caught:
        message = "INVALID FIELD: NAME"
        raise ValueError(message)

    # Match uses regex search; use an ordinary assertion for exact equality.
    assert_eq(str(caught.exception), "INVALID FIELD: NAME")


@test(mark="fast")
def test_structured_comparison_diagnostic() -> None:
    """A captured failure identifies the nested path, not just two large reprs."""
    with assert_raises(AssertionFailure) as mismatch:
        assert_eq({"types": ["int", "str"]}, {"types": ["int", "bytes"]})
    assert_in("At $['types'][1]: 'str' != 'bytes'", str(mismatch.exception))


def _parse_port(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        message = "invalid port"
        raise RuntimeError(message) from error


@test(mark="fast")
def test_exception_cause() -> None:
    with assert_raises(RuntimeError, msg="port conversion should fail") as caught:
        _ = _parse_port("not-a-number")

    assert_eq(str(caught.exception), "invalid port")
    cause = assert_isinstance(caught.exception.__cause__, ValueError)
    assert_in("not-a-number", str(cause))


@test(mark="fast")
def test_bound_narrowing_helpers() -> None:
    settings: dict[str, object] = {"suite": "SNEKTEST"}
    configured = assert_is_not_none(settings.get("suite"))
    name = assert_isinstance(configured, str)
    assert_eq(name.lower(), "snektest")
