# Slack event intake

A Saha Slack app is a bot/integration inside your existing workspace, not a duplicate
Slack desktop app. Create and install your own app using `slack-app-manifest.json`,
subject to your workspace's app-approval process. Use a dedicated app so another
integration's event routing is not changed.

The manifest receives app mentions, channel messages the bot can access, and DMs
with the bot. It does not grant access to all human-to-human DMs or to your personal
self-DM. The current transport receives events only; automatic outgoing replies
are not implemented.

1. Review the manifest in Slack's app management UI and install it in your workspace.
2. Enable Socket Mode. Create an app-level token with `connections:write` and obtain
   the installed bot token. Do not paste either token into a task, issue or repository.
3. Install the optional dependency in the same venv: `python -m pip install '.[slack]'`.
4. Save `{"app_token":"YOUR_APP_TOKEN","bot_token":"YOUR_BOT_TOKEN"}` in a private
   JSON file outside the clone. Restrict file permissions to your OS account.
5. Configure `integrations.slack` with `enabled: true`, your `team_id` and that file's
   absolute `credentials_file` path.
6. Enable the Slack responsibility and set explicit `channels` and `senders` arrays
   with your selected channel/DM and authorized sender IDs. Invite the bot where
   needed. Do not use an empty or workspace-wide default allowlist.
7. Restart Saha when idle. Send a deliberately scoped test event and inspect the
   receipt, task and resulting artifact. Connection alone is not end-to-end proof.

Socket Mode is push-based and does not require a public webhook URL. The native
Slack desktop read capability is separate: it can help with a particular task but
does not make the service automatically receive Slack events.

Official references: [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
and [Python SDK](https://docs.slack.dev/tools/python-slack-sdk/socket-mode/).
