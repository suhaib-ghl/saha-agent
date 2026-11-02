# Configuration and integrations

Use `config.example.json` as the reference. `saha init` copies the packaged version
to your own profile with every external integration disabled. Edit your private
config, run `saha doctor`, and restart when idle. Authentication, an enabled adapter
and a matching responsibility are separate requirements.

## Limits and responsibilities

`engine` chooses `demo`, `codex`, `claude`, `team` or `desktop`. Defaults: one worker,
600 seconds per job, three attempts per continuation budget and 20 job starts per
UTC day. Explicit retry or owner continuation resets attempts, not the daily cap.
Live tool approval stays in the same attempt. Team can invoke up to four children
within one job. Claude has a configurable per-invocation dollar budget; Codex has
no hard token/dollar cap here. API polling makes no model call while unchanged.

Responsibilities specify permitted sources, identity filters and task instructions.
They are the owner's standing scope. Retrieved messages never expand that scope.
External writes remain unavailable in this pilot. Core integration files must not
contain other people's task IDs or credentials.

## GitHub

Install `gh`, run `gh auth login` for your own account, and verify `gh api user`.
Configure `integrations.github.login` with that login. Add selected PRs:

```json
{"enabled":true,"login":"your-login","pull_requests":[{"repository":"your-org/your-repo","number":42}]}
```

Enable the GitHub responsibility and put the same repositories in its `repositories`
allowlist. Saha verifies identity, reads the current PR head/checks, and watches
selected PRs. It does not discover every repository or create/merge PRs. The bundled
adapter uses github.com; Enterprise host selection is not implemented.

## ClickUp

Create your own authorized ClickUp API token. Save only that token in a private file
outside the clone, restrict it to your OS account (for example, `chmod 600`), and
configure `integrations.clickup`:

```json
{"enabled":true,"workspace_id":"YOUR_WORKSPACE_ID","user_id":"YOUR_USER_ID","token_file":"~/.saha/clickup-token"}
```

Enable the ClickUp responsibility with the same `assignee_id`. Saha checks that the
token belongs to the configured user and fetches tasks assigned to that user. A
first scan establishes a baseline; subsequent changes can enqueue eligible work.
It excludes terminal task statuses. Workspace/team and user IDs come from your own
ClickUp account, not this repository.

For existing installations only, explicitly configured `credential_config` plus
`credential_server` can read a legacy MCP entry's `X-ClickUp-Token` header. There is
no default credential-file scan and no dependency on a specific company's MCP.

## Slack

See [Slack setup](SLACK-SETUP.md). Socket Mode uses your own installed Slack app and
an outbound WebSocket. Both channel and sender allowlists must be populated before
enabling a Slack responsibility. Saha does not install a shared app on your behalf.

## Native computer use (optional)

Install a compatible official Codex Computer Use host and enable its supported OS
permissions. Set `integrations.computer.enabled` to true and `manifest` to the
absolute path of that installation's MCP JSON manifest containing `cua_repl`.
Discover it from your installed plugin; do not copy another user's path or session
credentials. The manifest may change when the vendor updates the plugin.

`saha doctor` checks whether this manifest can be read. It does not prove app access.
Submit a **desktop** job limited to reading one visible app and saving a local
report. If the tool asks, answer **Live computer-use permission** in the dashboard.
That answer resolves the actual pending request in the same worker. Unsupported
forms are cancelled. New sessions may need new approval; no blanket grant is made.

The desktop adapter uses Codex's experimental app-server stdio protocol. It disables
inherited plugins/hooks/MCP servers, retains the workspace and command-network
sandbox, and checks the resulting tool inventory. Linux and Windows native desktop
access have not been verified. A native app connection does not imply browser access
or real-time Slack events. Never bypass a denied surface through a different tool.

## Webhook ingress

Supported signed endpoints are `/hooks/slack`, `/hooks/github`, `/hooks/clickup`.
The service process reads `SAHA_SLACK_SECRET`, `SAHA_GITHUB_SECRET` and
`SAHA_CLICKUP_SECRET`. These verify deliveries; they are not API access tokens.
The server binds only to loopback. Public delivery requires a separately managed
HTTPS ingress forwarding **only** the relevant hook routes. Never expose owner
dashboard/control routes. There is no automatic tunnel installer.

Signed payloads are deduplicated and filtered. Assignment evidence must be explicit.
API scanners separately verify current identities and state. Workers fetch current
source evidence through a read-only context broker, not by trusting webhook prose.

## Hooks, skills, plugins and MCP

The repository contains portable `skills/`, `commands/`, `hooks/`, `plugin.json`
and `.claude-plugin/` manifests. These are workflow instructions and read-only
inspection tools. Loading a plugin does **not** start the service or grant desktop
permissions. Plugin installation and slash-command support depend on the host.

After Python installation, register your venv's **absolute** `saha-mcp` executable
as a stdio MCP server. Set `SAHA_HOME` or `SAHA_STATE` in that server's environment
if you use a non-default profile. It offers `saha_status`, `saha_tasks` and
`saha_receipts`, and no mutation/approval tools. `saha-session-status` is a read-only
session-start hook. Repository plugin wrappers invoke `python3`; it must be 3.11+.

Optional `codex_model` and `claude_model` values select models available to your own
CLI account. No model subscriptions or secrets are provided by this project.
