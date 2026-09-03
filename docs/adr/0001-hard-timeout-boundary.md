# Hard timeouts require disposable process isolation

Snektest will not describe `--timeout` as a hard limit for synchronous test bodies or local collection and imports. A hard guarantee requires process-per-case isolation and separately isolated collection so a supervisor can terminate the stuck child. That design would replace the current in-process runner and persistent-worker fixture lifetimes. Until Snektest adopts it deliberately, users must set an external supervisor or CI job timeout around the complete command.
