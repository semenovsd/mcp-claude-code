"""MCP Claude Code - Simplified Interactive MCP Server for Claude Code CLI.

This package provides full interactive access to Claude Code CLI through MCP
with native IDE integration for permissions, choice questions, text input,
and confirmations.

Core Innovation:
    Custom JSON protocol via prompt augmentation teaches Claude to output
    interaction markers that are parsed in real-time and shown to users
    via native Cursor dialogs.

Example:
    >>> from mcp_claude_code.server import main
    >>> main()
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__license__ = "MIT"

__all__ = ["__version__"]
