#!/usr/bin/env python3
"""Embedded MCP server for permission approval via stdio.

This module is spawned as a subprocess by Claude Code when using
--permission-prompt-tool. It communicates with the main MCP server
via Unix socket to handle permission requests.

Usage:
    python approver.py <socket_path>

The server exposes a single MCP tool 'approve' that Claude Code calls
when it needs permission for an operation. The tool:
1. Receives tool_name and tool_input from Claude Code
2. Sends request to main server via Unix socket
3. Main server shows elicitation dialog to user
4. Returns {"behavior": "allow"} or {"behavior": "deny"} to Claude Code
"""

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


async def request_permission_via_socket(
    socket_path: str,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """Request permission decision from main server via Unix socket.

    Args:
        socket_path: Path to Unix socket
        tool_name: Name of tool requesting permission
        tool_input: Tool input parameters

    Returns:
        Dictionary with 'granted' bool and optional 'message' or 'decision'
    """
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)

        request = {
            "type": "permission_request",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

        logger.info(f"[Approver] Sending permission request for {tool_name}")
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()

        # Wait for response
        response_data = await asyncio.wait_for(reader.readline(), timeout=120.0)
        response = json.loads(response_data.decode())

        logger.info(f"[Approver] Received response: granted={response.get('granted')}")

        writer.close()
        await writer.wait_closed()

        return response

    except asyncio.TimeoutError:
        logger.error("[Approver] Timeout waiting for permission response")
        return {"granted": False, "message": "Permission request timed out"}

    except ConnectionRefusedError:
        logger.error(f"[Approver] Cannot connect to socket: {socket_path}")
        return {"granted": False, "message": "Cannot connect to permission server"}

    except Exception as e:
        logger.error(f"[Approver] Socket error: {e}")
        return {"granted": False, "message": f"Socket error: {e}"}


async def run_approver_server(socket_path: str) -> None:
    """Run MCP permission approver server.

    Args:
        socket_path: Path to Unix socket for communicating with main server
    """
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    server = Server("perm-approver")
    logger.info(f"[Approver] Starting MCP server, socket_path={socket_path}")

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

        # Request decision from main server via Unix socket
        decision = await request_permission_via_socket(socket_path, tool_name, tool_input)

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


def main() -> None:
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: approver.py <socket_path>", file=sys.stderr)
        sys.exit(1)

    socket_path = sys.argv[1]
    logger.info(f"[Approver] Starting with socket_path={socket_path}")

    try:
        asyncio.run(run_approver_server(socket_path))
    except KeyboardInterrupt:
        logger.info("[Approver] Interrupted")
    except Exception as e:
        logger.error(f"[Approver] Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
