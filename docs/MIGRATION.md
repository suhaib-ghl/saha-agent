# Migrating an existing private installation

Version 0.2.0 adds per-user defaults. Earlier local builds used `config.local.json`
and `.runtime/` inside the checkout. They remain usable through explicit CLI paths:

```sh
saha --config /absolute/path/to/config.local.json --state /absolute/path/to/.runtime serve
```

Existing services with explicit `--config` and `--state` keep their own records.
The update does not move, copy, delete or publish those records. Do not run an old
service and a new default-profile service accidentally. Stop/unload the old service
before migration. Never copy a live SQLite database piecemeal.

For an intentional migration, stop the runtime, back up the entire old state
directory privately (including SQLite sidecars if present), and copy it into your
chosen profile's `state/` directory. Copy your own config to that profile's
`config.json`. Inspect credential paths and restrictive permissions. Keep the old
backup until you verify tasks, receipts and artifacts. Paths recorded in historical
results can still point to the former directory.

The generic generated service label is now `io.saha.agent`. Unload the old label
before installing the replacement; service-file generation does not migrate a
running service. Explicit legacy ClickUp MCP credential extraction remains supported,
but a standalone private `token_file` removes that dependency for new installs.

The distributable repository starts from a clean source snapshot. Private pilot Git
history, screenshots, IDs, logs, account config and task records are not imported.
Sharing the repository never shares your working profile.
