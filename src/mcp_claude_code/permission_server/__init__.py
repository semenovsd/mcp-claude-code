"""Permission server package for handling Claude Code permission prompts.

This package provides the embedded permission server infrastructure that enables
native permission handling via --permission-prompt-tool. It consists of:

- ElicitationCallbackServer: Unix socket server running in main process,
  receives permission requests and calls ctx.elicit() to show UI dialogs.

- run_approver_server: Standalone MCP server spawned by Claude Code,
  communicates with callback server via Unix socket.
"""

from .approver import run_approver_server
from .callback_server import ElicitationCallbackServer

__all__ = ["run_approver_server", "ElicitationCallbackServer"]
