# Security and privacy

Saha is a single-user local pilot. It is not a multi-tenant service or a security
boundary against another process running as your OS user. The service and configured
MCP adapters can read their selected credentials. Use trusted CLIs/plugins and a
dedicated OS account or stronger isolation if your threat model requires it.

- Bind the dashboard to loopback; owner credentials authorize control operations.
  Do not expose it through a public tunnel or commit its owner/ingress keys.
- Keep API tokens in private files outside the repository. The release exporter
  includes only approved source paths; inspect all additions before publication.
- Workspaces and CLI restrictions limit worker behavior. They do not make malicious
  vendor tools safe. Provider authentication and model traffic are separate from
  command-network sandboxing.
- Incoming webhooks, messages, tickets and model results are untrusted evidence.
  Owner responsibility scopes and validated permissions control actions.
- Approval responses are single-use and bound to a current revision/execution.
  Task prose cannot grant tool access. Denied surfaces must not be bypassed.
- The pilot has no outbound messaging, merge, deployment, refund or customer-data
  deletion adapters. Expanding those capabilities requires explicit authorization
  and independent tests, not a more permissive prompt.
- Raw source context, Slack text and model outputs can be stored in private run logs
  and sent to the chosen model provider. Review your organization's policy before
  connecting work accounts. Keep sensitive content out of public bug reports.

For a vulnerability, use the repository's private vulnerability-reporting feature
if available. Otherwise ask the maintainer for a private reporting channel without
posting exploit details, credentials or private logs. Do not include customer data.
