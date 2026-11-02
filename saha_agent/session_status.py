"""Session hook: a read-only count, never an implicit worker launch."""
import json
from .mcp import read_tool
from .paths import state


def main():
    try:
        print('Saha local runtime status (read-only): ' + json.dumps(read_tool('saha_status', {}, state())))
        print('Only the owner dashboard can answer decisions. This hook does not start the service.')
    except Exception:
        print('Saha status unavailable. No work was started.')
