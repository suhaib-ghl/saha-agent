# Install and run Saha

Saha is a single-user local application. Each person installs it using their own OS
account, CLI logins, integration credentials and configuration. Do not copy someone
else's state directory or credentials.

## Requirements

| Component | Requirement |
|---|---|
| Runtime | Python 3.11+ on macOS or Linux |
| First-run demo | No model, account or API key required |
| Codex worker | Installed, authenticated Codex CLI; tested with 0.162.1 |
| Claude worker | Installed, authenticated Claude Code; tested with 2.1.316 |
| Team worker | Both CLIs; sequential engineer/reviewer workflow |
| Native desktop | Compatible official Codex Computer Use host, manifest and OS permissions; verified on macOS only |
| Windows | Native Windows is not supported. A Linux environment such as WSL2 can run the core, but Windows desktop control is not established |

The model CLIs and Computer Use host are separate products, not bundled dependencies.
Their accounts, billing, licenses and availability remain their own. Saha has no
subscription-sharing feature. Linux service generation is included; desktop behavior
is not assumed portable between OSes.

## Clone and install

```sh
git clone https://github.com/suhaib-ghl/saha-agent.git
cd saha-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
saha init
saha doctor
saha serve
```

Keep that terminal open. In another terminal, from the clone:

```sh
source .venv/bin/activate
saha open
```

`saha open` opens the local dashboard with its owner token in a URL fragment. The UI
removes the fragment and retains it in tab session storage. Do not share that link.
The default address is `http://127.0.0.1:7331`. If the port is occupied, change `port`
in your config before starting. Do not launch another copy against the same state.

## Prove the complete local workflow

```sh
saha submit 'Exercise the local demo approval and continuation.' --engine demo --title 'My first Saha job'
```

In the dashboard, answer the demo's task decision. The next invocation writes
`demo-result.md`. Open it, review it, then select **Mark reviewed**. This tests the
inbox, queue, decisions, artifacts and completion without any model calls. It does
not test Slack, GitHub, ClickUp, or computer access.

## Enable a real worker

Install and log into your chosen CLI following its official instructions. Confirm
it works interactively before using it through Saha. `saha doctor` checks installation,
not authentication. Select Codex or Claude Code in the dashboard, or submit explicitly:

```sh
saha submit 'Create a short project plan in plan.md and verify that file.' --engine codex
```

To change the default, edit `engine` in `~/.saha/config.json` and restart when idle.
`saha init --engine codex` selects Codex only when creating a new config; init never
overwrites an existing config. Team mode requires both engines. Desktop mode needs
the additional setup in [Configuration](CONFIGURATION.md).

## Files and profiles

- `~/.saha/config.json`: private settings and selected identities; no bundled credentials.
- `~/.saha/state/ledger.sqlite3`: durable tasks, owner memory, decisions and receipts.
- `~/.saha/state/workspaces/`: one isolated directory per task.
- `~/.saha/state/runs/`: private detailed worker logs.
- `~/.saha/state/owner.key`: dashboard authority; never give this to a worker.

Set `SAHA_HOME` for a different profile directory. `SAHA_CONFIG` and `SAHA_STATE`
override those individual defaults; CLI `--config` and `--state` take precedence.
Global CLI flags go **before** the command. Example:

```sh
SAHA_HOME="$PWD/my-private-profile" saha init
SAHA_HOME="$PWD/my-private-profile" saha serve
```

Use the same profile for `open`, `status`, hooks and the read-only MCP. A separate
profile that runs concurrently also needs a different port. Profiles are independent
local instances, not a shared multi-tenant server. Keep profiles outside your Git clone.

## Background startup

First verify foreground operation. `service-file` generates a definition; it never
installs a service or changes OS permissions. It captures the current Python
interpreter, config, state and PATH. Regenerate after moving the install or venv.

On macOS, with the default profile:

```sh
saha service-file --format launchd
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/.saha/state/io.saha.agent.plist" "$HOME/Library/LaunchAgents/"
# Stop the foreground server before loading this:
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.saha.agent.plist"
```

To stop and unload: `launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.saha.agent.plist"`.
Launchd uses KeepAlive; a plain `saha stop` will cause this service to restart. Use
`saha pause` to stop work while retaining the listener, or bootout to unload it.

On a Linux system using systemd user services:

```sh
saha service-file --format systemd
mkdir -p "$HOME/.config/systemd/user"
cp "$HOME/.saha/state/io.saha.agent.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
# Stop the foreground server before starting this:
systemctl --user enable --now io.saha.agent
```

To stop and disable: `systemctl --user disable --now io.saha.agent`.
Use `--label` for distinct service names if running multiple profiles. The host must
remain available. Sleep, logout, a locked UI or an unavailable desktop host can stop
computer work; a background service does not remove those constraints.

## Upgrade and uninstall

Pause work and wait for the active child to stop; unload any background service.
Back up your private profile locally. Then `git pull --ff-only` and rerun
`.venv/bin/python -m pip install .`. Review [CHANGELOG](../CHANGELOG.md), run doctor,
then start the service and resume. Do not overwrite your configuration with the
example during an upgrade. Schema additions are automatic; interrupted jobs are
not silently replayed.

To uninstall, unload the service, run `.venv/bin/python -m pip uninstall saha-agent`,
and remove the service definition and clone if desired. Your private profile is
retained. Review it yourself before deleting history, credentials or artifacts.
