"""Standalone MCP entry point, including when installed from a wheel."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from saha_agent.context_mcp import main

main()
