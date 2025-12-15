#!/usr/bin/env python3
"""Simple script to run MCP server in SSE mode for testing."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from mcp_claude_code.server import mcp

if __name__ == "__main__":
    # Run with SSE transport
    mcp.run(transport="sse", port=8765)
