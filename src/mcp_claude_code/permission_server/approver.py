#!/usr/bin/env python3
"""Embedded MCP server for permission approval via stdio.

This module is spawned as a subprocess by Claude Code when using
--permission-prompt-tool. It communicates with the main MCP server
via Unix socket to handle permission requests.

Usage:
    python approver.py <socket_path> [--timeout SECONDS] [--retries N] [--retry-delay SECONDS]

The server exposes a single MCP tool 'approve' that Claude Code calls
when it needs permission for an operation. The tool:
1. Receives tool_name and tool_input from Claude Code
2. Sends request to main server via Unix socket (with retry logic)
3. Main server shows elicitation dialog to user
4. Returns {"behavior": "allow"} or {"behavior": "deny"} to Claude Code
"""

import argparse
import asyncio
import json
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Default settings (can be overridden via command line)
DEFAULT_PERMISSION_TIMEOUT = 3600  # 60 minutes - user may be away
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 0.1  # seconds


async def request_permission_via_socket(
    socket_path: str,
    tool_name: str,
    tool_input: dict,
    timeout_seconds: float = DEFAULT_PERMISSION_TIMEOUT,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> dict:
    """Request permission decision from main server via Unix socket with retry logic.

    Args:
        socket_path: Path to Unix socket
        tool_name: Name of tool requesting permission
        tool_input: Tool input parameters
        timeout_seconds: Timeout for waiting for user response (60 min default)
        retry_attempts: Number of connection retry attempts
        retry_delay: Base delay between retries (exponential backoff)

    Returns:
        Dictionary with 'granted' bool and optional 'message' or 'decision'
    """
    last_error: Exception | None = None

    for attempt in range(retry_attempts):
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)

            request = {
                "type": "permission_request",
                "tool_name": tool_name,
                "tool_input": tool_input,
            }

            logger.info(f"[Approver] Sending permission request for {tool_name} (attempt {attempt + 1}/{retry_attempts})")
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()

            # Wait for response with long timeout (user may be away)
            response_data = await asyncio.wait_for(
                reader.readline(),
                timeout=timeout_seconds,
            )
            response = json.loads(response_data.decode())

            logger.info(f"[Approver] Received response: granted={response.get('granted')}")

            writer.close()
            await writer.wait_closed()

            return response

        except ConnectionRefusedError as e:
            last_error = e
            logger.warning(
                f"[Approver] Connection refused (attempt {attempt + 1}/{retry_attempts}): {socket_path}"
            )
            if attempt < retry_attempts - 1:
                delay = retry_delay * (2 ** attempt)  # Exponential backoff
                logger.info(f"[Approver] Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
            continue

        except asyncio.TimeoutError:
            logger.error(f"[Approver] Timeout ({timeout_seconds}s) waiting for permission response")
            return {"granted": False, "message": f"Permission request timed out after {timeout_seconds}s"}

        except json.JSONDecodeError as e:
            logger.error(f"[Approver] Invalid JSON response: {e}")
            return {"granted": False, "message": f"Invalid response from server: {e}"}

        except Exception as e:
            last_error = e
            logger.error(f"[Approver] Socket error: {e}")
            if attempt < retry_attempts - 1:
                delay = retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            continue

    # All retries exhausted
    error_msg = str(last_error) if last_error else "Unknown error"
    logger.error(f"[Approver] All {retry_attempts} connection attempts failed: {error_msg}")
    return {"granted": False, "message": f"Cannot connect to permission server after {retry_attempts} attempts: {error_msg}"}


async def run_approver_server(
    socket_path: str,
    timeout_seconds: float = DEFAULT_PERMISSION_TIMEOUT,
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> None:
    """Run MCP permission approver server.

    Args:
        socket_path: Path to Unix socket for communicating with main server
        timeout_seconds: Timeout for waiting for user permission response
        retry_attempts: Number of connection retry attempts
        retry_delay: Base delay between retries
    """
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    server = Server("perm-approver")
    logger.info(f"[Approver] Starting MCP server, socket_path={socket_path}")
    logger.info(f"[Approver] Settings: timeout={timeout_seconds}s, retries={retry_attempts}, delay={retry_delay}s")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """List available tools."""
        return [
            types.Tool(
                name="approve",
                description="Approve or deny permission for tool execution",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Name of tool requesting permission",
                        },
                        "tool_input": {
                            "type": "object",
                            "description": "Tool input parameters",
                            "additionalProperties": True,
                        },
                        "input": {
                            "type": "object",
                            "description": "Tool input parameters (alias for tool_input)",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["tool_name"],
                    "additionalProperties": True,
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        """Handle tool calls."""
        # Log raw arguments for debugging - critical for understanding Claude Code's format
        logger.info(f"[Approver] RAW ARGUMENTS: {json.dumps(arguments, default=str)}")

        if name != "approve":
            logger.warning(f"[Approver] Unknown tool requested: {name}")
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"behavior": "deny", "message": "Unknown tool"}),
                )
            ]

        tool_name = arguments.get("tool_name", "unknown")

        # Try multiple parameter names - Claude Code CLI format is not fully documented
        # Priority: tool_input (our schema) -> input (Agent SDK style) -> fallback
        tool_input = arguments.get("tool_input") or arguments.get("input")

        # Fallback: extract all arguments except tool_name as tool_input
        if not tool_input:
            tool_input = {k: v for k, v in arguments.items() if k not in ("tool_name", "tool_input", "input")}

        # Ensure tool_input is always a dict
        if not isinstance(tool_input, dict):
            tool_input = {}

        logger.info(f"[Approver] Permission request for tool: {tool_name}")
        logger.info(f"[Approver] Extracted tool_input: {json.dumps(tool_input, default=str)}")

        # Request decision from main server via Unix socket (with retry and long timeout)
        decision = await request_permission_via_socket(
            socket_path,
            tool_name,
            tool_input,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
        )

        if decision.get("granted"):
            result = {
                "behavior": "allow",
                "updatedInput": tool_input,
            }
            logger.info(f"[Approver] Allowing {tool_name}")
        else:
            result = {
                "behavior": "deny",
                "message": decision.get("message", "Permission denied"),
            }
            logger.info(f"[Approver] Denying {tool_name}: {result.get('message')}")

        return [types.TextContent(type="text", text=json.dumps(result))]

    # Run stdio server
    async with stdio_server() as (read_stream, write_stream):
        logger.info("[Approver] Running stdio server...")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="MCP permission approver server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "socket_path",
        help="Path to Unix socket for communicating with main server",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PERMISSION_TIMEOUT,
        help="Timeout in seconds for waiting for user permission response",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRY_ATTEMPTS,
        help="Number of connection retry attempts",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY,
        help="Base delay in seconds between retries (exponential backoff)",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    logger.info(f"[Approver] Starting with socket_path={args.socket_path}")
    logger.info(f"[Approver] timeout={args.timeout}s, retries={args.retries}, retry_delay={args.retry_delay}s")

    try:
        asyncio.run(run_approver_server(
            socket_path=args.socket_path,
            timeout_seconds=args.timeout,
            retry_attempts=args.retries,
            retry_delay=args.retry_delay,
        ))
    except KeyboardInterrupt:
        logger.info("[Approver] Interrupted")
    except Exception as e:
        logger.error(f"[Approver] Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
