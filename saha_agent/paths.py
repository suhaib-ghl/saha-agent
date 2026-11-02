"""Per-user paths shared by the CLI, MCP server and session hook."""
import os
from pathlib import Path


def home():
    return Path(os.environ.get('SAHA_HOME', Path.home() / '.saha')).expanduser().resolve()


def state():
    return Path(os.environ.get('SAHA_STATE', home() / 'state')).expanduser().resolve()


def config():
    return Path(os.environ.get('SAHA_CONFIG', home() / 'config.json')).expanduser().resolve()
