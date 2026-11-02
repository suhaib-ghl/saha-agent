# Verification

Release candidate: **0.2.0**, 2 November 2026. Test records in this repository use
synthetic data; private pilot receipts and conversations are not distributed.

## Portable core

- **64 Python tests passed locally** on macOS with Python 3.14.6.
- Python compilation and JavaScript syntax checks passed.
- Built a wheel and installed it into a fresh venv. Ran from a temporary directory
  outside the checkout with Python isolated mode and a fresh `SAHA_HOME`.
- The installed package initialized a demo profile, served the packaged dashboard,
  ingested a synthetic job, waited for a scoped owner answer, wrote an artifact,
  reached review, and recorded completion. Pause/resume/shutdown passed.
- Installed read-only MCP saw that same profile; the packaged context-MCP entry point
  started successfully without relying on repository-relative scripts.
- Both service-definition formats were generated using the active interpreter and
  chosen profile. Generation is not proof that systemd or launchd was installed on
  another machine.
- Tests cover profile isolation, non-overwriting initialization, matching packaged
  defaults, explicit identity/scopes, token-file selection, no implicit credential
  scan, loopback startup without DNS, local CLI requests without HTTP proxies, and release exclusion of private state, screenshots and configuration.
- Existing tests cover duplicate events, signed ingress, sender/assignment filtering,
  stale completion rejection, task and live-tool approvals, expiry/replay/origin
  checks, cancellation, process timeout, runtime exclusivity and bounded team repair.

The GitHub Actions workflow runs unit tests, compilation, JavaScript syntax, wheel
installation, the complete demo smoke and source packaging on macOS and Linux with
Python 3.11 and 3.13. Check its actual run status; the presence of a workflow does
not itself mean it has passed.

## Live capabilities observed during development

Codex 0.162.1 and Claude Code 2.1.316 produced local artifacts in an earlier pilot.
A bounded engineer/reviewer workflow and current source-context reads were exercised.
On a compatible macOS host, an actual owner Computer Use approval reached the
waiting app-server worker, which successfully read visible native Slack text and
saved a verified report. A transient ScreenCaptureKit error occurred on an earlier
attempt; its root cause was not proven. No private content is included here.

These observations establish specific tested paths, not compatibility with every
version, OS, account or permission configuration. New installers must authenticate
and verify their own sources and app permissions. CI uses no real Slack, GitHub,
ClickUp, model or desktop credentials. No native Windows desktop check, Linux CUA
check, production deployment, outbound send, live voice call or permanent unattended
computer-access guarantee is claimed.

## Reproduce

```sh
python -m unittest discover -s tests -v
python -m compileall -q saha_agent scripts tests
node --check saha_agent/web/app.js
python -m pip wheel . --no-deps --wheel-dir dist
python -m venv /tmp/saha-install-test
/tmp/saha-install-test/bin/python -m pip install dist/saha_agent-0.2.0-py3-none-any.whl
python scripts/install_smoke.py /tmp/saha-install-test/bin/python
python scripts/package.py
```

Use an unused temporary venv path. The smoke test creates and cleans its own isolated
profile, uses a dynamically selected loopback port, and makes no model calls.
