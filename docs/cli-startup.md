# Standalone CLI startup policy

The installed `snektest` executable, `python -m snektest`, and the compatibility
`python -m snektest.cli` entry point enable Python's context-aware warnings by
default, before collection. Async warning-capture contexts then remain isolated
across tasks; context copying in `asyncio.to_thread` also preserves them. This
sets only `context_aware_warnings`, not `thread_inherit_context` or warning filters.

## Explicit startup choices

Python's own startup settings override the CLI default:

- `python -X context_aware_warnings=0 -m snektest ...` explicitly opts out.
- `PYTHON_CONTEXT_AWARE_WARNINGS=0` opts out for an installed executable too.
- Explicit `-X` takes precedence over the environment, as in Python itself.
- `-E` and `-I` ignore the environment setting. Without an explicit `-X` choice,
  the CLI default is therefore enabled even if the ignored variable contains `0`.
- An empty environment value is not an explicit choice.
- Already-enabled interpreters and explicit opt-outs are not restarted.

There is no Snektest configuration key or additional CLI flag for this policy.
Use Python's startup controls when a test deliberately needs legacy warnings.

## Relaunch and process behavior

`_bootstrap.py` owns the policy at standalone entry points. If the interpreter
started with its disabled default and no explicit choice, the bootstrap relaunches
`sys.executable` with `-X context_aware_warnings=1` and the original interpreter
arguments. It does not merely change the environment of a running interpreter.
The explicit startup option also prevents another relaunch, including with `-E`
or `-I`; no environment sentinel is required.

The relaunch retains the venv/interpreter, interpreter options, argument
boundaries, working directory, stdin, stdout/stderr, and command exit status.
Streams are flushed before relaunch. On POSIX, `execv` replaces the process,
preserving its PID and inherited ignored signal dispositions. Snektest does not
reset ignored signals as part of this feature. On Windows, a waiting parent keeps
the console and inherited streams and returns the child's exit status; console
Ctrl+C can reach both processes, so the parent waits for the child's completion
rather than reporting a separate interruption. This does not introduce hard
process deadlines or guarantee cleanup after termination.

Original workers, the fixture host, and replacement workers inherit the selected
Python startup policy through standard multiprocessing spawn behavior. Bootstrap
is not performed separately for each test or worker.

Relaunch precedes Snektest collection and user test imports. Interpreter/site
startup has already happened once and may run again. This cannot retroactively
change or quarantine arbitrary startup-hook effects or output. Launching with
`-X context_aware_warnings=1` already selected avoids relaunch when startup hooks
must not repeat.

## Library use

Importing Snektest, calling `run_tests_programmatic`, or directly calling the
compatibility `snektest.cli.main` function does not restart an embedding process
or change its startup policy/environment. The embedding application chooses its
own Python flags. Executing a module as `__main__` intentionally selects standalone
CLI behavior, including through `runpy`; it is not the embedding interface.

## Validation

`tests/meta/test_cli_startup.py` exercises subprocess entry points, precedence,
embedding, streams/options/selectors, worker replacement, unsuccessful exits,
and interruption. `tests/meta/test_distribution_artifacts.py` separately installs
the built wheel and source archive into fresh environments using locked
production constraints and executes their real console scripts and isolated
module entry points. Native external POSIX signal delivery is tested separately
from portable in-process SIGINT handling. Windows console broadcast behavior
requires native Windows validation; Linux tests cannot establish it.
