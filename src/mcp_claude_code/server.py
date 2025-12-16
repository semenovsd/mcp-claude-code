"""FastMCP server for Claude Code proxy."""

import asyncio
import logging
import os
import signal
import sys
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

logger = logging.getLogger(__name__)

from .config import Settings
from .executor.interactive_executor import InteractiveExecutor
from .storage.permission_manager import PermissionManager

mcp = FastMCP("claude-code-proxy")
settings = Settings()

# Track active executors for graceful shutdown
_active_executors: list[InteractiveExecutor] = []
_shutdown_event: asyncio.Event | None = None


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

    # Track executor for graceful shutdown
    _active_executors.append(executor)

    try:
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
    finally:
        # Remove executor from tracking
        if executor in _active_executors:
            _active_executors.remove(executor)


async def _graceful_shutdown(sig: signal.Signals) -> None:
    """Handle graceful shutdown on signal.

    Args:
        sig: Signal that triggered shutdown
    """
    logger.info(f"Received {sig.name}, initiating graceful shutdown...")

    # Terminate all active executors
    for executor in _active_executors[:]:  # Copy list to avoid modification during iteration
        logger.info(f"Terminating active executor...")
        try:
            await executor._terminate_process()
            await executor._cleanup_permission_server()
        except Exception as e:
            logger.warning(f"Error during executor cleanup: {e}")

    logger.info("Graceful shutdown complete")

    # Exit
    sys.exit(0)


def _setup_signal_handlers() -> None:
    """Setup signal handlers for graceful shutdown.
    
    Uses signal.signal() to work before the event loop is running.
    The handler schedules the async shutdown task on the running event loop.
    """
    def _signal_handler(sig: int) -> None:
        """Synchronous signal handler that schedules async shutdown."""
        signal_enum = signal.Signals(sig)
        logger.info(f"Received {signal_enum.name}, scheduling graceful shutdown...")
        
        # Get the running event loop (FastMCP's loop)
        try:
            loop = asyncio.get_running_loop()
            # Schedule the async shutdown task
            loop.create_task(_graceful_shutdown(signal_enum))
        except RuntimeError:
            # No event loop running yet - this shouldn't happen, but handle gracefully
            logger.warning("No event loop running, cannot schedule graceful shutdown")
            sys.exit(1)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
            logger.debug(f"Registered signal handler for {sig.name}")
        except (ValueError, OSError) as e:
            # Windows or other platform issues
            logger.debug(f"Signal handler for {sig.name} not supported: {e}")


def main() -> None:
    """Main entry point for MCP server."""
    logger.info("Starting MCP Claude Code server...")
    logger.info(f"Settings: permission_timeout={settings.permission_timeout_seconds}s, "
                f"socket_retries={settings.socket_retry_attempts}")

    # Setup signal handlers before starting the server
    # This uses signal.signal() which works synchronously and will schedule
    # async shutdown tasks on FastMCP's event loop when signals are received
    _setup_signal_handlers()

    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()