# Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard disconnected | Start the service, check its terminal/log, and use the configured port. Run `saha status` with the same profile. |
| Dashboard asks to connect | Run `saha open`; do not paste owner keys into tasks or issues. |
| Port already in use | Stop the existing instance or select a different port. Never start two instances against the same state. |
| Another runtime owns state | Inspect the existing service; do not delete the runtime lock to force concurrency. |
| Job stays queued | Check pause state, active job and daily UTC cap. Limits are not model-specific billing quotas. |
| Attempt budget exhausted | Inspect prior receipts and artifacts; fix the cause, then explicitly retry. Owner task continuations reset attempts but not daily usage. |
| CLI installed but job fails | Verify its login interactively, supported version and model availability. Doctor's `--version` check is not a login test. |
| Task decision approved but app denied | Task answers do not grant tool permission. Desktop requests must appear as Live computer-use permission and reach the same running worker. |
| Live request expired | Inspect the task, then retry deliberately. Expired or old approvals cannot be reused. |
| Computer use partial | Discovery or a single successful task does not prove all apps, browser access or permanent approval. |
| Native capture error | Keep the Mac awake and session available; inspect the official host/OS error. A capture error is distinct from an app authorization denial. Avoid repeated blind retries. |
| Browser unavailable in background | Requires a compatible authorized host. Do not copy another session's identities or use native browser control to bypass a denied surface. |
| Slack needs setup while native Slack reads work | Push intake and native app control are different connections. Configure Socket Mode for new-event delivery. |
| Source connects but no old jobs appear | First API scan records a baseline; only subsequent eligible changes enqueue. Check responsibility and identity filters. |
| ClickUp/GitHub context denied | Confirm account identity and task/repository scope. Do not broaden access just to make the error disappear. |
| Task interrupted after sleep/restart | Review local files and receipts before retrying; the service does not assume whether uncertain work finished. |

The **Task decision** card asks for missing business information. **Live computer-use
permission** answers an actual tool request. Computer tool failures are blocked with
the real error rather than converted into repeated permission-looking task questions.

Private logs are under your profile's `state/runs/`. They may contain source data and
model context. Share only a redacted error and reproduction, never the whole profile.
No dangerous approval/sandbox bypass is needed for the supported approval flow.
