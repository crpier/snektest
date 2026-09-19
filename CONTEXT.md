# Snektest

Snektest runs tests and owns fixture lifetimes and their cleanup.

## Language

**Task owner**:
The test or fixture responsible for a spawned task's lifetime and cleanup diagnostics. A fixture remains responsible for its tasks even when setup fails before yielding.

**Owned-task cleanup**:
Cancellation and reaping of tasks belonging to one task owner, including descendants created during cancellation. Unrelated embedding-application tasks are outside that responsibility.

**Failed fixture setup**:
A fixture invocation that ends before supplying its value. Its setup error and any task-cleanup failures are separate diagnostics; established dependencies retain their own lifetimes.

**Run-fixture descriptor**:
The value supplied by a command-wide fixture. Each execution worker receives an independent copy while the fixture host retains the original for teardown.

**Publication batch**:
A group of run-fixture requests resolved before any waiting test resumes. Each request has its own success or failure outcome.
