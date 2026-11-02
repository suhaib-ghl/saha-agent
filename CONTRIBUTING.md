# Contributing

Start with an issue describing a reproducible problem or a bounded extension.
Use synthetic identities and data. Never add real account IDs, tokens, conversations,
screenshots of private workspaces, or runtime databases.

Use Python 3.11+ on macOS or Linux, create a venv, and run `python -m pip install -e .`.
Run `python -m unittest discover -s tests -v`, Python compilation and the optional
Node syntax check shown in README. Add meaningful tests for state transitions,
source scopes, permission handling and process lifecycle changes. Include a wheel
install smoke test when changing packaging or resource paths.

Keep `config.example.json` and the packaged default in `saha_agent/defaults/` identical.
Keep version fields in pyproject, Python and plugin manifests in sync. Document
changes in CHANGELOG. Preserve existing config/state during upgrades. Do not weaken
approval checks, sandbox restrictions, replay protection or origin checks to make a
test pass. Use only synthetic credentials in tests and CI.

The `scripts/package.py` exporter uses a source allowlist. Update its allowlist only
for intentional distributable files, and inspect the resulting archive. Plugin/MCP
host compatibility and external integration claims need actual evidence separate
from unit tests.
