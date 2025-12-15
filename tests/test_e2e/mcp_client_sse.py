"""Real MCP client for E2E testing using HTTP/SSE transport.

This client connects to a REAL MCP server via HTTP/SSE and supports Elicitation.
NO MOCKS!

Features:
- Connects to REAL MCP server via HTTP/SSE
- Supports Elicitation for interactive testing
- Configurable responses for testing different scenarios
- Tracks elicitation history for verification
"""

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, types
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


@dataclass
class ElicitationRecord:
    """Record of an elicitation request and response."""
    message: str
    requested_schema: dict | None
    response: str
    response_action: str  # "accept" or "decline"


class RealMCPClientSSE:
    """Real MCP client that connects to actual MCP server via HTTP/SSE.

    This is NOT a mock! It:
    - Connects to REAL MCP server via HTTP
    - Uses SSE (Server-Sent Events) for streaming
    - Supports Elicitation (unlike stdio)
    - Calls REAL tools (execute_claude)
    - Returns REAL results

    Testing features:
    - set_elicitation_answer(): Override responses for specific questions
    - set_permission_response(): Set how to respond to permission requests
    - get_elicitation_history(): Review what elicitations occurred
    - reset_elicitation_answers(): Reset to default answers
    """

    # Default answers for common questions
    DEFAULT_ANSWERS = {
        "What is your name?": "Ivan",
        "What is the project name?": "myproject",
        "Which package manager?": "poetry",
        "Which language?": "Python",
    }

    # Default permission response
    DEFAULT_PERMISSION_RESPONSE = "Allow Once"

    def __init__(self):
        """Initialize client."""
        self.session: ClientSession | None = None
        self.server_url: str | None = None

        # Configurable answers for elicitation requests
        self.elicitation_answers: dict[str, str] = self.DEFAULT_ANSWERS.copy()

        # Permission response (Allow Once, Allow Session, Allow Always, Deny)
        self.permission_response: str = self.DEFAULT_PERMISSION_RESPONSE

        # Track elicitation history for test verification
        self.elicitation_history: list[ElicitationRecord] = []

    # =========================================================================
    # TEST CONFIGURATION METHODS
    # =========================================================================

    def set_elicitation_answer(self, question: str, answer: str) -> None:
        """Set a custom answer for a specific question.

        Use this in tests to control how the client responds to Claude's questions.

        Args:
            question: Question text (or substring to match)
            answer: Answer to provide

        Example:
            mcp_client.set_elicitation_answer("project name", "my-awesome-project")
        """
        self.elicitation_answers[question] = answer
        logger.info(f"📝 Set elicitation answer: '{question}' -> '{answer}'")

    def set_permission_response(self, response: str) -> None:
        """Set how to respond to permission requests.

        Args:
            response: One of "Allow Once", "Allow Session", "Allow Always", "Deny"

        Example:
            # Test permission denial
            mcp_client.set_permission_response("Deny")

            # Test persistent permissions
            mcp_client.set_permission_response("Allow Always")
        """
        valid_responses = {"Allow Once", "Allow Session", "Allow Always", "Deny"}
        if response not in valid_responses:
            raise ValueError(f"Invalid permission response: {response}. Must be one of {valid_responses}")

        self.permission_response = response
        logger.info(f"🔐 Set permission response: '{response}'")

    def reset_elicitation_answers(self) -> None:
        """Reset elicitation answers to defaults.

        Call this after tests that modify elicitation behavior.
        """
        self.elicitation_answers = self.DEFAULT_ANSWERS.copy()
        self.permission_response = self.DEFAULT_PERMISSION_RESPONSE
        logger.info("🔄 Reset elicitation answers to defaults")

    def clear_elicitation_history(self) -> None:
        """Clear the elicitation history.

        Call this at the start of a test if you need a clean history.
        """
        self.elicitation_history = []
        logger.info("🧹 Cleared elicitation history")

    def get_elicitation_history(self) -> list[ElicitationRecord]:
        """Get the history of all elicitation requests and responses.

        Returns:
            List of ElicitationRecord objects

        Example:
            history = mcp_client.get_elicitation_history()
            assert len(history) == 2
            assert history[0].response == "Allow Once"
        """
        return self.elicitation_history.copy()

    def get_permission_requests(self) -> list[ElicitationRecord]:
        """Get only permission-related elicitation requests.

        Returns:
            List of ElicitationRecord objects that were permission requests
        """
        return [
            r for r in self.elicitation_history
            if any(kw in r.message for kw in ["Allow Write", "Allow Read", "Allow Bash", "Allow Edit", "Permission"])
        ]

    def get_question_requests(self) -> list[ElicitationRecord]:
        """Get only question-related elicitation requests (non-permission).

        Returns:
            List of ElicitationRecord objects that were questions
        """
        return [
            r for r in self.elicitation_history
            if not any(kw in r.message for kw in ["Allow Write", "Allow Read", "Allow Bash", "Allow Edit", "Permission"])
        ]

    # =========================================================================
    # ELICITATION HANDLER
    # =========================================================================

    async def _handle_elicitation_request(
        self, context: Any, params: types.ElicitRequestParams
    ) -> types.ElicitResult | types.ErrorData:
        """Handle elicitation/create request from server.

        This method is called by the MCP client when the server requests
        user input (permission, question, choice, etc.)

        Args:
            context: Request context from MCP
            params: Elicitation request parameters with message and requestedSchema

        Returns:
            ElicitResult with user's answer
        """
        message = params.message
        requested_schema = getattr(params, "requestedSchema", None)

        logger.info("=" * 60)
        logger.info(f"📥 ELICITATION REQUEST")
        logger.info(f"   Message: {message}")
        logger.info(f"   Schema: {requested_schema}")
        logger.info("=" * 60)

        # Determine if this is a permission request
        is_permission_request = any(
            keyword in message
            for keyword in ["Allow Write", "Allow Read", "Allow Bash", "Allow Edit", "Permission", "allow"]
        )

        if is_permission_request:
            answer = self.permission_response
            logger.info(f"🔐 Permission request detected")
            logger.info(f"   Responding with: {answer}")
        else:
            # Find matching answer from configured answers
            answer = "default_answer"
            matched_question = None

            for question, response in self.elicitation_answers.items():
                if question.lower() in message.lower():
                    answer = response
                    matched_question = question
                    break

            if matched_question:
                logger.info(f"❓ Question matched: '{matched_question}'")
            else:
                logger.info(f"❓ No matching question found, using default")
            logger.info(f"   Responding with: {answer}")

        # Determine action based on answer
        if answer == "Deny":
            action = "decline"
        else:
            action = "accept"

        # Record this elicitation for test verification
        record = ElicitationRecord(
            message=message,
            requested_schema=requested_schema,
            response=answer,
            response_action=action,
        )
        self.elicitation_history.append(record)
        logger.info(f"📊 Elicitation #{len(self.elicitation_history)} recorded")

        # Return appropriate result
        if action == "decline":
            logger.info(f"📤 Returning: action=decline")
            return types.ElicitResult(action="decline", content=None)
        else:
            logger.info(f"📤 Returning: action=accept, value={answer}")
            return types.ElicitResult(action="accept", content={"value": answer})

    # =========================================================================
    # CONNECTION MANAGEMENT
    # =========================================================================

    @asynccontextmanager
    async def connect(self, server_url: str):
        """Connect to MCP server via HTTP/SSE.

        Args:
            server_url: URL of SSE server, e.g. "http://localhost:8000/sse"

        Example:
            async with client.connect("http://localhost:8000/sse"):
                result = await client.call_execute_claude("Create hello.txt")
        """
        self.server_url = server_url
        logger.info(f"🔌 Connecting to SSE server: {server_url}")

        # Clear history on new connection
        self.clear_elicitation_history()

        # Create SSE client
        async with sse_client(server_url) as (read, write):
            # Create ClientSession with elicitation callback
            self.session = ClientSession(
                read,
                write,
                elicitation_callback=self._handle_elicitation_request
            )

            await self.session.__aenter__()
            await self.session.initialize()

            logger.info("✅ SSE client connected and initialized with elicitation support")

            # List available tools
            tools_result = await self.session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            logger.info(f"🛠️ Available tools: {tool_names}")

            try:
                yield self
            finally:
                await self.disconnect()

    async def disconnect(self):
        """Disconnect from server."""
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"⚠️ Error during disconnect: {e}")
            self.session = None
            logger.info("🔌 SSE client disconnected")

    # =========================================================================
    # TOOL CALLS
    # =========================================================================

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
            model: Model to use (haiku, sonnet, opus)
            workspace_root: Working directory for file operations
            enable_permissions: Enable permission requests via native
                --permission-prompt-tool mechanism
            enable_choice_questions: Enable choice questions protocol
            enable_text_questions: Enable text questions protocol
            enable_confirmations: Enable confirmation dialogs protocol

        Returns:
            Result dictionary with:
            - success: bool
            - output: str (Claude's response)
            - error: str | None
            - permissions_requested: int
            - permissions_granted: int
            - choices_asked: int
            - questions_asked: int
            - confirmations_asked: int

        Example:
            result = await client.call_execute_claude(
                prompt="Create hello.txt with 'Hello World'",
                workspace_root="/tmp/test",
                enable_permissions=False,
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

        logger.info("=" * 70)
        logger.info(f"🚀 CALLING execute_claude")
        logger.info(f"   Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        logger.info(f"   Workspace: {workspace_root}")
        logger.info(f"   Permissions: {enable_permissions}")
        logger.info(f"   Choices: {enable_choice_questions}")
        logger.info(f"   Questions: {enable_text_questions}")
        logger.info("=" * 70)

        result = await self.session.call_tool("execute_claude", arguments=arguments)

        # Extract result from content
        if result.content and len(result.content) > 0:
            content = result.content[0]
            if hasattr(content, "text"):
                text = content.text
                if not text or not text.strip():
                    logger.error("❌ execute_claude returned empty text content")
                    return {"success": False, "error": "Empty response from tool"}

                try:
                    result_json = json.loads(text)
                    logger.info(f"✅ execute_claude completed:")
                    logger.info(f"   Success: {result_json.get('success')}")
                    logger.info(f"   Permissions requested: {result_json.get('permissions_requested', 0)}")
                    logger.info(f"   Choices asked: {result_json.get('choices_asked', 0)}")
                    logger.info(f"   Questions asked: {result_json.get('questions_asked', 0)}")
                    if result_json.get('error'):
                        logger.error(f"   Error: {result_json.get('error')}")
                    return result_json
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse JSON: {e}")
                    logger.error(f"   Raw text: {text[:500]}")
                    return {"success": False, "error": f"Invalid JSON response: {e}"}
            elif isinstance(content, dict):
                logger.info(f"✅ execute_claude returned dict: {json.dumps(content, indent=2)}")
                return content

        logger.error("❌ execute_claude returned no content")
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
