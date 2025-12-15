"""FastMCP server for Claude Code proxy."""

import logging
import os
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP, Context
from pydantic import Field

# Enable DEBUG logging for detailed diagnostics
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("fastmcp").setLevel(logging.DEBUG)
logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("mcp_claude_code").setLevel(logging.DEBUG)

from .config import Settings
from .executor.interactive_executor import InteractiveExecutor
from .storage.permission_manager import PermissionManager

mcp = FastMCP("claude-code-proxy")
settings = Settings()


@mcp.tool(
    annotations={
        "title": "Claude Code",
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": True,
    }
)
async def execute_claude(
    prompt: Annotated[str, Field(description="Task for Claude Code")],
    model: Annotated[str, Field(
        default="haiku",
        description="haiku=fast/simple, sonnet=complex, opus=critical"
    )] = "haiku",
    workspace_root: Annotated[str | None, Field(
        default=None,
        description="Working directory (default: $WORKSPACE_ROOT)"
    )] = None,
    skip_permissions: Annotated[bool, Field(
        default=False,
        description="Skip permission checks for autonomous mode"
    )] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delegate coding tasks to Claude Code CLI.
    Interactive: permissions, questions, confirmations.
    Model: haiku=fast/simple, sonnet=complex, opus=critical.
    """
    # Resolve workspace
    if workspace_root:
        workspace = Path(workspace_root)
    elif env_root := os.getenv("WORKSPACE_ROOT"):
        workspace = Path(env_root)
    elif settings.workspace_root:
        workspace = Path(settings.workspace_root)
    else:
        workspace = Path.cwd()

    # Initialize components
    permission_manager = PermissionManager(workspace)
    executor = InteractiveExecutor(
        settings=settings,
        permission_manager=permission_manager,
        ctx=ctx,
    )

    # Execute with all interactive features enabled
    result = await executor.execute(
        prompt=prompt,
        model=model,
        workspace_root=workspace,
        enable_permissions=not skip_permissions,
        enable_choices=True,
        enable_questions=True,
        enable_confirmations=True,
    )

    return result


def main() -> None:
    """Main entry point for MCP server."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
