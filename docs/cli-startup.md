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

## POSIX signal policy (#108)

**Preserve launcher intent, not normalize signals.** Snektest adds no signal-reset
flag or configuration key. Foreground versus background placement does not itself
select a Snektest policy; the dispositions supplied by the launcher do. This is
not a claim that inherited ignores violate POSIX. The Python interpreter and
`asyncio.Runner` still provide their ordinary interrupt handling.

| SIGINT on entry | Standalone CLI and test-created children |
| --- | --- |
| Normal/default | CPython installs its interrupt handler. While running the event loop, asyncio can temporarily install its cooperative handler. Ordinary interruption exits 2; JSON mode emits an `interrupted` error document when shutdown completes. Fresh Python children normally install their own default interrupt handler. |
| Ignored (`SIG_IGN`) | Remains ignored through bootstrap exec, collection, execution, spawned host/workers, and ordinary subprocess exec. Sending SIGINT does not request cleanup or stop an ignored run. |
| Custom caught handler | An exec into the CLI resets caught handlers according to POSIX, and Python normally installs its default SIGINT handler. A handler installed in the CLI interpreter before entry is left alone if no relaunch is needed. Bootstrap exec and worker spawn do not transport Python callables. Use `-X context_aware_warnings=1` when entering in-process with a deliberate custom handler. |

Noninteractive POSIX shells commonly start asynchronous jobs with SIGINT and
SIGQUIT ignored. `nohup` principally ignores **SIGHUP**; it is not a SIGINT reset
and is not interchangeable with shell background-job behavior. Snektest does not
reset SIGQUIT or SIGHUP either. Ignored dispositions survive exec/spawn; caught
handlers do not. Default SIGQUIT/SIGHUP may terminate the process without Python
cleanup or a final JSON document. Terminal Ctrl+C usually targets the foreground
process group, not one PID; `kill -INT PID` only targets that PID. Snektest does
not forward signals to, or contain, arbitrary application subprocess trees.

### Explicitly interruptible background launch

This tested noninteractive `/bin/sh` recipe resets **only SIGINT** in a Python
launcher before exec, even if the shell itself inherited ignored SIGINT:

```sh
python -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execv(sys.executable, [sys.executable, "-m", "snektest", *sys.argv[1:]])' --json-output tests/ </dev/null >snektest.json 2>snektest.err &
runner=$!
# Later, to request interruption of the coordinator:
kill -INT "$runner"
wait "$runner"
```

Use the desired virtual environment's `python`. SIGQUIT/SIGHUP keep their own
launcher policy; add `nohup` only if ignoring SIGHUP is also intentional. A shell
`trap` reset alone is not a portable replacement: signals ignored on shell entry
may remain ignored. When preserving the background shell's intent is desired,
launch `python -m snektest ... &` without the reset wrapper instead.

### Runner interruption versus application signal tests

Local cooperative async interruption attempts fixture/task cleanup under the
configured cleanup budget (60 seconds when body timeouts are disabled). It is
not a hard deadline for synchronous teardown, imports, blocking threads, or
arbitrary sync/CPU-bound work. A SIGINT sent only to the coordinator in explicit
worker mode can terminate workers during shutdown: worker/session/run fixture
cleanup is not guaranteed on this path, even if the coordinator emits valid
interruption JSON. Process-group delivery may also interrupt children directly.
Do not infer fixture completion from the presence of the JSON error document.
Repeated/hard interruption and SIGKILL can prevent cleanup and final output.

Application shutdown tests should establish their **child's** signal policy
explicitly, rather than changing the runner's global dispositions. For a Python
application, a launcher can set SIGINT to SIG_DFL immediately before exec (as
above), or the application can install its own handler during startup. Avoid
`preexec_fn` in a threaded runner. A runner's cooperative handler is not inherited
as a Python callable by an exec'd child; an ignored disposition is. Always use
bounded readiness/shutdown waits and reap application children yourself.

Importing Snektest and running its programmatic API neither resets ignored
signals nor replaces custom handlers with a standalone policy. asyncio may
still temporarily manage normal SIGINT during an embedding application's own
`asyncio.run`; host event-loop/signal ownership remains with the application.

### Signal verification boundaries

`tests/meta/test_cli_startup.py` checks explicit ignores, actual noninteractive
shell background launches, the SIGINT-only reset recipe, custom handlers across
bootstrap exec/no-exec, embedding, application children, and coordinator-only
external interruption in local/worker JSON runs. Local cooperative cleanup is
verified; worker termination is not advertised as graceful fixture shutdown.
The independently installed wheel and source-archive tests also verify inherited
ignores through their real entry points. POSIX tests do not establish Windows
console control-event or process-group behavior.
