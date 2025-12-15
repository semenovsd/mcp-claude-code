"""Real MCP client for E2E testing.

This client connects to a REAL MCP server via stdio and calls REAL tools.
NO MOCKS!
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class RealMCPClient:
    """Real MCP client that connects to actual MCP server.

    This is NOT a mock! It:
    - Spawns REAL MCP server process
    - Connects via REAL MCP protocol (stdio)
    - Calls REAL tools (execute_claude)
    - Returns REAL results
    """

    def __init__(self):
        """Initialize client."""
        self.session: ClientSession | None = None
        self._read_stream = None
        self._write_stream = None

    @asynccontextmanager
    async def connect(self, server_command: list[str]):
        """Connect to MCP server.

        Args:
            server_command: Command to start server, e.g. ["python", "-m", "mcp_claude_code.server"]

        Yields:
            Connected client session

        Example:
            async with client.connect(["python", "-m", "mcp_claude_code.server"]) as session:
                result = await client.call_execute_claude("Create hello.txt")
        """
        server_params = StdioServerParameters(
            command=server_command[0],
            args=server_command[1:],
            env=None,
        )

        logger.info(f"Starting MCP server: {' '.join(server_command)}")

        async with stdio_client(server_params) as (read, write):
            self._read_stream = read
            self._write_stream = write

            async with ClientSession(read, write) as session:
                self.session = session
                await session.initialize()

                logger.info("MCP client connected and initialized")

                # List available tools
                tools_result = await session.list_tools()
                logger.info(f"Available tools: {[t.name for t in tools_result.tools]}")

                yield self

                logger.info("MCP client disconnecting")

    async def call_execute_claude(
        self,
        prompt: str,
        model: str = "sonnet",
        workspace_root: str | None = None,
        enable_permissions: bool = True,
        enable_choice_questions: bool = False,
        enable_text_questions: bool = False,
        enable_confirmations: bool = False,
    ) -> dict[str, Any]:
        """Call execute_claude tool on REAL MCP server.

        Args:
            prompt: Task prompt for Claude
            model: Model to use
            workspace_root: Working directory
            enable_permissions: Enable permission requests
            enable_choice_questions: Enable choice questions
            enable_text_questions: Enable text questions
            enable_confirmations: Enable confirmations

        Returns:
            Result from execute_claude tool

        Example:
            result = await client.call_execute_claude(
                prompt="Create hello.txt",
                enable_permissions=True,
            )
            assert result["success"] == True
        """
        if not self.session:
            raise RuntimeError("Client not connected. Use 'async with client.connect(...)'")

        arguments = {
            "prompt": prompt,
            "model": model,
            "enable_permissions": enable_permissions,
            "enable_choice_questions": enable_choice_questions,
            "enable_text_questions": enable_text_questions,
            "enable_confirmations": enable_confirmations,
        }

        if workspace_root:
            arguments["workspace_root"] = workspace_root

        logger.info(f"Calling execute_claude with prompt: {prompt[:50]}...")

        result = await self.session.call_tool("execute_claude", arguments=arguments)

        # Extract result from content
        if result.content and len(result.content) > 0:
            content = result.content[0]
            if hasattr(content, "text"):
                text = content.text
                if not text or not text.strip():
                    logger.error(f"execute_claude returned empty text content")
                    return {"success": False, "error": "Empty response from tool"}

                try:
                    result_json = json.loads(text)
                    logger.info(f"execute_claude returned: {json.dumps(result_json, indent=2)}")
                    return result_json
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")
                    logger.error(f"Raw text: {text[:500]}")
                    return {"success": False, "error": f"Invalid JSON response: {e}"}
            elif isinstance(content, dict):
                logger.info(f"execute_claude returned: {json.dumps(content, indent=2)}")
                return content

        logger.error("execute_claude returned no content")
        return {"success": False, "error": "No content in response"}

    async def list_tools(self) -> list[str]:
        """List available tools from server.

        Returns:
            List of tool names
        """
        if not self.session:
            raise RuntimeError("Client not connected")

        tools_result = await self.session.list_tools()
        return [t.name for t in tools_result.tools]
