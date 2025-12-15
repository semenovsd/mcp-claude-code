"""Unix socket server for permission elicitation callbacks.

This server runs in the main MCP server process and handles permission
requests from the embedded approver server via Unix socket.
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class ElicitationCallbackServer:
    """Handles permission requests from embedded approver via Unix socket.

    This server runs in the main process and:
    1. Receives permission requests from the embedded approver subprocess
    2. Calls the elicitation callback (which triggers ctx.elicit())
    3. Returns the user's decision back to the approver

    Attributes:
        elicitation_callback: Async function to handle permission requests
        permission_manager: Permission storage manager
        socket_path: Path to Unix socket file
    """

    def __init__(
        self,
        elicitation_callback: Callable[[str, dict], Awaitable[dict]],
        permission_manager: Any | None = None,
    ) -> None:
        """Initialize callback server.

        Args:
            elicitation_callback: Async function that receives (tool_name, tool_input)
                and returns dict with 'granted' bool and optional 'message' or 'decision'
            permission_manager: Optional permission manager for caching
        """
        self.elicitation_callback = elicitation_callback
        self.permission_manager = permission_manager
        self.socket_path: str | None = None
        self._server: asyncio.Server | None = None

    async def start(self) -> str:
        """Start Unix socket server.

        Returns:
            Path to the Unix socket file
        """
        # Generate unique socket path
        self.socket_path = str(
            Path(tempfile.gettempdir()) / f"mcp-perm-{id(self)}.sock"
        )

        # Clean up any existing socket file
        Path(self.socket_path).unlink(missing_ok=True)

        # Start server
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
        )

        logger.info(f"[CallbackServer] Started Unix socket server at {self.socket_path}")
        return self.socket_path

    async def stop(self) -> None:
        """Stop server and cleanup socket file."""
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception as e:
                logger.warning(f"[CallbackServer] Error waiting for server close: {e}")

        if self.socket_path:
            try:
                Path(self.socket_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"[CallbackServer] Error removing socket file: {e}")

        logger.info("[CallbackServer] Stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle incoming permission request from approver.

        Args:
            reader: Stream reader for incoming data
            writer: Stream writer for response
        """
        try:
            # Read request
            data = await asyncio.wait_for(reader.readline(), timeout=60.0)
            if not data:
                logger.warning("[CallbackServer] Empty request received")
                return

            request = json.loads(data.decode())
            logger.info(f"[CallbackServer] Received request: {request.get('type')}")

            # Process request
            if request.get("type") == "permission_request":
                tool_name = request.get("tool_name", "unknown")
                tool_input = request.get("tool_input", {})

                logger.info(f"[CallbackServer] Permission request for {tool_name}")

                # Call elicitation callback
                response = await self.elicitation_callback(tool_name, tool_input)
            else:
                response = {"granted": False, "message": "Invalid request type"}

            # Send response
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()

            logger.info(f"[CallbackServer] Sent response: granted={response.get('granted')}")

        except asyncio.TimeoutError:
            logger.error("[CallbackServer] Request timed out")
            try:
                error_response = {"granted": False, "message": "Request timed out"}
                writer.write(json.dumps(error_response).encode() + b"\n")
                await writer.drain()
            except Exception:
                pass

        except json.JSONDecodeError as e:
            logger.error(f"[CallbackServer] Invalid JSON: {e}")
            try:
                error_response = {"granted": False, "message": f"Invalid JSON: {e}"}
                writer.write(json.dumps(error_response).encode() + b"\n")
                await writer.drain()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[CallbackServer] Error handling request: {e}")
            try:
                error_response = {"granted": False, "message": f"Internal error: {e}"}
                writer.write(json.dumps(error_response).encode() + b"\n")
                await writer.drain()
            except Exception:
                pass

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
