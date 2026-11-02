# Architecture

Saha is a small coordinator, not a continuously thinking model. The Python service
owns scheduling and persistence. Model CLIs only run for claimed jobs.

1. `events.py` verifies signed ingress, normalizes payloads, and applies owner scopes.
   `connections.py` reads selected APIs; `slack_socket.py` handles optional push intake.
2. `store.py` commits deduplication and enqueueing together. A runtime OS lock and a
   single worker prevent concurrent execution against one state directory.
3. `runtime.py` claims one revision with a lease, bounds job starts, collects artifacts,
   and accepts completion only from the matching current execution.
4. `engines.py` starts isolated CLI workers. Team mode supplies an engineer result to
   a read-only reviewer and permits one bounded repair. It is not recursive delegation.
5. `app_server.py` handles Codex desktop jobs through bidirectional JSON-RPC. Supported
   tool requests are displayed to the owner, bound to the task revision and lease,
   and answered once. Expired, cancelled or superseded requests cannot grant access.
6. `server.py` serves a loopback dashboard with owner authentication and origin checks.
   `mcp.py` exposes a separate read-only ledger interface with no decision authority.

## State and memory

Events, tasks, task decisions, live tool requests, execution receipts, scoped owner
instructions and feedback live in SQLite. Artifacts and raw CLI logs are files in
the private state directory. Source cursor baselines prevent a first-run backlog
replay. New source revisions invalidate stale task decisions. Cancelled or stale
workers cannot commit success. Restarted running tasks become interrupted, requiring
inspection before retry. Database additions are idempotent and preserve old records.

Owner feedback changes the next handoff. Only explicit saved instructions become
standing memory. There is no automatic learning from arbitrary Slack statements or
self-modification of permissions. `ready_for_review` is a worker claim plus an
artifact inventory; it is not proof of acceptance until reviewed.

## Extension points

- **New engine:** add a command builder, bounded process lifecycle and result parser;
  test its filesystem, credential and permission boundaries. Match the structured
  result contract. Do not assume another CLI inherits Codex's Computer Use tools.
- **New source:** verify sender/transport, normalize a stable event ID and correlation,
  implement explicit owner filters and current-context reads, then test duplicates,
  changed assignments and failure recovery. Never use source prose as authority.
- **Outbound actions:** require a separate scoped authorization and action receipt
  design. The current adapters deliberately have no send/merge/deploy tools.
- **UI/voice:** browser dictation/read-aloud are optional conveniences. Realtime calls
  and unattended contact are not implemented.

The `jev.py` scorer is an optional, disabled prototype and is not wired into intake.
It does not decide authority or extend responsibilities. Avoid presenting it as a
production integration without protocol and end-to-end validation.

Skills, plugins, hooks and MCP package capabilities; they do not replace the running
event receiver, durable state, execution loop or permission lifecycle.
