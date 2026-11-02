# Changelog

## 0.2.0 — 2026-11-02

First portable source release under the MIT license.

### Added

- Installable Python package and `saha`, `saha-mcp`, `saha-session-status` commands.
- Per-user configuration/state defaults, environment overrides and first-run demo.
- Generic macOS launchd and Linux systemd user-service generation.
- Standalone ClickUp token-file configuration and explicit integration validation.
- Installation, configuration, architecture, security, troubleshooting, migration
  and contribution documentation; macOS/Linux CI and clean install checks.
- Source-allowlisted exports and a clean public Git history without pilot data.
- Live Codex app-server tool approvals, bound to the active execution and delivered
  through the owner dashboard.

### Fixed

- Loopback startup no longer depends on reverse DNS; local CLI credentials are never routed through environment HTTP proxies.

- Task continuation no longer exhausts attempts accumulated before an owner answer.
- Tool approval no longer masquerades as a model question that restarts a worker.
- Stale leases cannot commit success or permanently block unrelated queued jobs.
- Source context entry point and packaged UI/defaults work after a wheel installation.

### Changed

- Removed author-specific workspace, sender, repository and assignment defaults.
- External integrations start disabled; no private credential store is scanned by default.
- Private pilot reports, screenshots and account configuration are excluded from
  distributable source. Original local records remain outside the public repository.

### Limitations

- Native Computer Use verified only on a compatible macOS host; new sessions may
  require approval. Browser and always-on availability are not guaranteed.
- GitHub/ClickUp use 30-second API polling; Slack Socket Mode supports push events.
- No production deployment, outbound messages, automatic ticket closure or universal
  provider/OS support. See README and VERIFICATION for the tested boundaries.

## 0.1.0 — private pilot

Initial durable queue, owner dashboard, isolated Codex/Claude workers, bounded team
review, source context, read-only MCP and optional Slack intake. Personal pilot
history is intentionally not part of the public repository.
