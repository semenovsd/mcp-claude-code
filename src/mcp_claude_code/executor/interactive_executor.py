"""Interactive executor for Claude Code CLI."""

import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models.events import ClaudeEventType
from ..models.interactions import PermissionDecision
from ..permission_server.callback_server import ElicitationCallbackServer
from ..prompts import get_system_prompt
from ..storage.permission_manager import PermissionManager
from .interaction_handler import InteractionHandler
from .stream_parser import (
    StreamParser,
    extract_text_content,
    format_progress_message,
    parse_result_event,
)

logger = logging.getLogger(__name__)


class InteractiveExecutor:
    """Orchestrates Claude Code CLI execution with interactive capabilities.

    Responsibilities:
    - Spawn Claude Code CLI subprocess
    - Manage stdin/stdout/stderr pipes
    - Coordinate between StreamParser and InteractionHandler
    - Handle process lifecycle (start, monitor, terminate)
    - Aggregate execution results

    Attributes:
        settings: Application settings
        permission_manager: Permission storage manager
        ctx: MCP context for elicitation
        interaction_handler: Handler for all interactions
        process: Running subprocess
        parser: Stream parser
        metrics: Execution metrics
    """

    def __init__(
        self,
        settings: Settings,
        permission_manager: PermissionManager,
        ctx: Any,
    ) -> None:
        """Initialize executor.

        Args:
            settings: Application settings
            permission_manager: Permission manager
            ctx: MCP Context for elicitation
        """
        self.settings = settings
        self.permission_manager = permission_manager
        self.ctx = ctx
        self.interaction_handler = InteractionHandler(ctx=ctx)

        self.process: asyncio.subprocess.Process | None = None
        self.parser: StreamParser | None = None

        # Session tracking for multi-turn
        self.session_id: str | None = None
        self.workspace_root: Path | None = None
        self.model: str = "sonnet"

        # Permission handling (always native when enabled + ctx available)
        self.enable_permissions: bool = True
        self.callback_server: ElicitationCallbackServer | None = None
        self.approver_config_path: str | None = None

        # Execution metrics
        self.permissions_requested = 0
        self.permissions_granted = 0
        self.choices_asked = 0
        self.questions_asked = 0
        self.confirmations_asked = 0

    async def execute(
        self,
        prompt: str,
        model: str = "sonnet",
        workspace_root: Path | None = None,
        enable_permissions: bool = True,
        enable_choices: bool = False,
        enable_questions: bool = False,
        enable_confirmations: bool = False,
        max_execution_seconds: int = 600,
        inactivity_timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Execute Claude Code with interactive capabilities.

        Args:
            prompt: Task prompt for Claude
            model: Model to use (haiku, sonnet, opus)
            workspace_root: Working directory
            enable_permissions: Enable permission requests (uses native --permission-prompt-tool)
            enable_choices: Enable choice questions
            enable_questions: Enable text questions
            enable_confirmations: Enable confirmations
            max_execution_seconds: Maximum total execution time
            inactivity_timeout_seconds: Inactivity timeout

        Returns:
            Dictionary with success, output, error, metrics, etc.
        """
        # Store for session resumption
        self.workspace_root = workspace_root
        self.model = model
        self.enable_permissions = enable_permissions

        # Setup native permission server if enabled and ctx is available
        use_native_permissions = enable_permissions and self.ctx is not None
        if use_native_permissions:
            logger.info("[InteractiveExecutor] Setting up native permission server")
            await self._setup_permission_server()

        try:
            # 1. Get system prompt for interaction protocols
            # Permissions are handled natively via --permission-prompt-tool
            system_prompt = get_system_prompt(
                enable_choices=enable_choices,
                enable_questions=enable_questions,
                enable_confirmations=enable_confirmations,
            )

            # 2. Build command with system prompt
            cmd = self._build_command(
                model, workspace_root,
                resume_session_id=None,
                system_prompt=system_prompt,
            )

            # 3. Spawn process
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_root) if workspace_root else None,
            )

            # 4. Initialize parser
            self.parser = StreamParser(self.process.stdout)

            # 5. Send user prompt via stdin (clean, without augmentation)
            await self._send_stdin_message(prompt)

            # 6. Main event loop
            try:
                result = await asyncio.wait_for(
                    self._event_loop(inactivity_timeout_seconds),
                    timeout=max_execution_seconds,
                )
                return result
            except asyncio.TimeoutError:
                await self._terminate_process()
                return {
                    "success": False,
                    "error": f"Execution exceeded {max_execution_seconds}s timeout",
                    "returncode": -1,
                    "permissions_requested": self.permissions_requested,
                    "permissions_granted": self.permissions_granted,
                    "choices_asked": self.choices_asked,
                    "questions_asked": self.questions_asked,
                    "confirmations_asked": self.confirmations_asked,
                }

        finally:
            # Cleanup native permission server resources
            await self._cleanup_permission_server()

    async def _event_loop(self, inactivity_timeout: int) -> dict[str, Any]:
        """Main event processing loop.

        Args:
            inactivity_timeout: Seconds of inactivity before timeout

        Returns:
            Execution result dictionary
        """
        output_buffer = []
        last_activity = time.time()
        pending_resume_response = None  # Track if we need to resume
        start_time = time.time()

        # Heartbeat mechanism - sends progress every 5 seconds
        heartbeat_interval = 5
        heartbeat_count = 0
        heartbeat_running = True

        async def heartbeat_task():
            nonlocal heartbeat_count
            while heartbeat_running:
                await asyncio.sleep(heartbeat_interval)
                if not heartbeat_running:
                    break
                heartbeat_count += 1
                elapsed = int(time.time() - start_time)
                if self.ctx:
                    try:
                        await self.ctx.report_progress(
                            progress=heartbeat_count,
                            total=None,
                            message=f"⏳ Still working... ({elapsed}s elapsed)",
                        )
                    except Exception as e:
                        logger.debug(f"Heartbeat progress report failed: {e}")

        # Start heartbeat in background
        heartbeat = asyncio.create_task(heartbeat_task())
        logger.debug("[InteractiveExecutor] Starting event loop with heartbeat")

        try:
            async for event in self.parser.parse_events():
                last_activity = time.time()

                logger.info(f"[InteractiveExecutor] ⚡ EVENT RECEIVED: {event.type}")
                logger.debug(f"[InteractiveExecutor] Event data keys: {list(event.data.keys()) if hasattr(event, 'data') else 'NO DATA'}")

                # Log full message content for ASSISTANT events to see tool uses
                if event.type == ClaudeEventType.ASSISTANT and "message" in event.data:
                    message = event.data.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content", [])
                        logger.info(f"[InteractiveExecutor] 📦 Message content blocks: {len(content) if isinstance(content, list) else 0}")
                        if isinstance(content, list):
                            for i, block in enumerate(content):
                                if isinstance(block, dict):
                                    block_type = block.get("type", "unknown")
                                    logger.info(f"[InteractiveExecutor]   Block {i}: type={block_type}")
                                    if block_type == "tool_use":
                                        logger.info(f"[InteractiveExecutor]     Tool: {block.get('name', 'unknown')}")
                                        logger.info(f"[InteractiveExecutor]     Input: {block.get('input', {})}")
                                    elif block_type == "text":
                                        text = block.get("text", "")
                                        logger.info(f"[InteractiveExecutor]     Text: {text[:200]}")

                # Extract session_id if present
                if "session_id" in event.data:
                    self.session_id = event.data["session_id"]
                    logger.info(f"[InteractiveExecutor] 📋 SESSION_ID captured: {self.session_id}")

                # Update progress
                await self._report_progress(event)

                # Handle interactions
                logger.debug(f"[InteractiveExecutor] Calling interaction_handler.handle_event for {event.type}")
                interaction_response = await self.interaction_handler.handle_event(event)
                if interaction_response:
                    logger.info(f"[InteractiveExecutor] 💬 INTERACTION HANDLED: type={interaction_response['type']}, text={interaction_response['text'][:100]}")

                    # Update metrics
                    self._update_metrics(interaction_response["type"])
                    logger.info(f"[InteractiveExecutor] 📊 Metrics: permissions={self.permissions_requested}, "
                              f"choices={self.choices_asked}, questions={self.questions_asked}, "
                              f"confirmations={self.confirmations_asked}")

                    # For multi-turn: wait for process to exit, then restart with --resume
                    if self.session_id:
                        logger.info(f"[InteractiveExecutor] 🔄 Preparing to resume session {self.session_id}")

                        # Format response with context for better Claude understanding
                        pending_resume_response = self._format_response_with_context(interaction_response)
                        logger.info(f"[InteractiveExecutor] 💾 Saved response for resume: {pending_resume_response[:100]}")

                        # Continue processing events until process naturally exits
                        logger.info(f"[InteractiveExecutor] ⏳ Continuing to process events until process exits...")
                    else:
                        logger.warning("[InteractiveExecutor] ⚠️ No session_id available for multi-turn - falling back to stdin")
                        await self._send_stdin_message(interaction_response["text"])
                else:
                    logger.debug(f"[InteractiveExecutor] No interaction detected in {event.type} event")

                # Accumulate output
                text_content = extract_text_content(event)
                if text_content:
                    output_buffer.append(text_content)
                    logger.info(f"[InteractiveExecutor] 📝 Claude output: {text_content[:500]}")

                # Check for completion
                if event.type == ClaudeEventType.RESULT:
                    logger.info("[InteractiveExecutor] ✅ RESULT event received - execution complete")

                    # Check if we need to resume before returning
                    if pending_resume_response and self.session_id:
                        logger.info(f"[InteractiveExecutor] 🔄 RESULT received but need to resume - breaking to resume session")
                        break  # Exit loop to trigger resumption

                    # No resumption needed - return final result
                    result = parse_result_event(event)

                    # Use output_buffer if result output is empty
                    if not result.get("output") and output_buffer:
                        result["output"] = "\n".join(output_buffer)
                        logger.info(f"[InteractiveExecutor] Using accumulated output_buffer ({len(output_buffer)} parts)")

                    result.update(
                        {
                            "permissions_requested": self.permissions_requested,
                            "permissions_granted": self.permissions_granted,
                            "choices_asked": self.choices_asked,
                            "questions_asked": self.questions_asked,
                            "confirmations_asked": self.confirmations_asked,
                        }
                    )
                    logger.info(f"[InteractiveExecutor] Final metrics: {result}")
                    logger.info(f"[InteractiveExecutor] Final output length: {len(result.get('output', ''))}")
                    return result

                # Check inactivity timeout
                if time.time() - last_activity > inactivity_timeout:
                    logger.warning(f"[InteractiveExecutor] ⏰ Inactivity timeout after {inactivity_timeout}s")
                    await self._terminate_process()
                    return {
                        "success": False,
                        "error": f"Inactivity timeout after {inactivity_timeout}s",
                        "output": "\n".join(output_buffer),
                        "returncode": -1,
                        "permissions_requested": self.permissions_requested,
                        "permissions_granted": self.permissions_granted,
                    }

        finally:
            # Stop heartbeat when done
            heartbeat_running = False
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        # Process ended - check if we need to resume
        if pending_resume_response and self.session_id:
            logger.info(f"[InteractiveExecutor] 🔄 Event loop ended, now resuming with session {self.session_id}")

            # Terminate current process before resuming
            if self.process and self.process.returncode is None:
                logger.info(f"[InteractiveExecutor] 🛑 Terminating current process...")
                await self._terminate_process()
                logger.info(f"[InteractiveExecutor] ✅ Process terminated")
            else:
                logger.info(f"[InteractiveExecutor] Process already exited (returncode={self.process.returncode if self.process else 'None'})")

            # Resume with the user's response
            logger.info(f"[InteractiveExecutor] 📞 Calling _resume_session with response: {pending_resume_response[:100]}")
            result = await self._resume_session(pending_resume_response)
            logger.info(f"[InteractiveExecutor] ✅ _resume_session completed, result keys: {list(result.keys()) if result else 'None'}")
            if result:
                return result

        # Process ended without resumption needed
        returncode = await self.process.wait()
        stderr = await self.process.stderr.read()

        return {
            "success": returncode == 0,
            "output": "\n".join(output_buffer),
            "error": stderr.decode() if stderr else None,
            "returncode": returncode,
            "permissions_requested": self.permissions_requested,
            "permissions_granted": self.permissions_granted,
            "choices_asked": self.choices_asked,
            "questions_asked": self.questions_asked,
            "confirmations_asked": self.confirmations_asked,
        }

    def _build_command(
        self,
        model: str,
        workspace_root: Path | None,
        resume_session_id: str | None = None,
        system_prompt: str | None = None,
    ) -> list[str]:
        """Build Claude Code CLI command.

        Args:
            model: Model name
            workspace_root: Working directory
            resume_session_id: Optional session ID to resume
            system_prompt: Optional system prompt for interaction protocols

        Returns:
            Command list for subprocess
        """
        cmd = [
            self.settings.claude_code_path,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
        ]

        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])

        # System prompt for interaction protocols (choice/question/confirmation)
        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])

        # Permission handling - native or bypass
        if not self.enable_permissions:
            # Complete bypass - no permission checks at all
            cmd.append("--dangerously-skip-permissions")
        elif self.approver_config_path:
            # Native permission handling via --permission-prompt-tool
            # Use --strict-mcp-config to ignore other MCP configs and use only ours
            cmd.extend([
                "--strict-mcp-config",
                "--mcp-config", self.approver_config_path,
                "--permission-prompt-tool", "mcp__perm__approve",
            ])
            logger.info(f"[InteractiveExecutor] Using native permissions with config: {self.approver_config_path}")

        return cmd

    async def _send_stdin_message(self, text: str) -> None:
        """Send a message to Claude via stdin.

        Args:
            text: Message text to send
        """
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }

        json_line = json.dumps(message) + "\n"
        self.process.stdin.write(json_line.encode())
        await self.process.stdin.drain()

    async def _report_progress(self, event: Any) -> None:
        """Report progress to MCP client with informative messages.

        Uses format_progress_message() to show detailed information about
        what Claude is currently doing (e.g., "📖 Read: src/main.py").

        Filters out permission-related events to prevent duplicate notifications
        (permissions are handled via elicitation callback).

        Args:
            event: Claude event
        """
        if self.ctx:
            # Skip permission events - they are handled via elicitation callback
            # Reporting them here would cause duplicate dialogs/notifications
            if self._is_permission_event(event):
                logger.debug("[InteractiveExecutor] Skipping progress for permission event")
                return

            # Use the new formatter for human-readable messages
            message = format_progress_message(event)
            await self.ctx.report_progress(
                progress=self.permissions_requested + self.choices_asked,
                total=None,
                message=message,
            )

    def _update_metrics(self, interaction_type: str) -> None:
        """Update execution metrics.

        Args:
            interaction_type: Type of interaction (permission, choice, question, confirmation)
        """
        if interaction_type == "permission":
            self.permissions_requested += 1
            self.permissions_granted += 1
        elif interaction_type == "choice":
            self.choices_asked += 1
        elif interaction_type == "question":
            self.questions_asked += 1
        elif interaction_type == "confirmation":
            self.confirmations_asked += 1

    def _is_permission_event(self, event: Any) -> bool:
        """Check if event is a permission tool call that should not be reported.

        Permission events are handled via the elicitation callback, so reporting
        them as progress would cause duplicate notifications to the client.

        Args:
            event: Claude event to check

        Returns:
            True if this is a permission-related event that should be filtered
        """
        if not hasattr(event, "type") or event.type != ClaudeEventType.ASSISTANT:
            return False

        tool_uses = event.get_tool_uses() if hasattr(event, "get_tool_uses") else []
        if not tool_uses:
            return False

        # Check if any tool is the permission approval tool
        for tu in tool_uses:
            name = tu.name or ""
            # Filter permission-related tools (e.g., mcp__perm__approve, approve)
            if "approve" in name.lower() or "perm" in name.lower():
                return True

        return False

    async def _resume_session(self, user_response: str) -> dict[str, Any] | None:
        """Resume session with user's response.

        Args:
            user_response: User's answer to send to Claude

        Returns:
            Execution result dictionary, or None if continuation needed
        """
        # Build resume command
        cmd = self._build_command(self.model, self.workspace_root, resume_session_id=self.session_id)

        # Spawn new process
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root) if self.workspace_root else None,
        )

        # Initialize new parser
        self.parser = StreamParser(self.process.stdout)

        # Send user's response
        logger.info(f"[InteractiveExecutor] 📤 Sending user response to resumed session: {user_response[:200]}")
        await self._send_stdin_message(user_response)

        # Continue processing events from resumed session
        output_buffer = []
        last_activity = time.time()
        start_time = time.time()

        # Heartbeat mechanism - sends progress every 5 seconds
        heartbeat_interval = 5
        heartbeat_count = 0
        heartbeat_running = True

        async def heartbeat_task():
            nonlocal heartbeat_count
            while heartbeat_running:
                await asyncio.sleep(heartbeat_interval)
                if not heartbeat_running:
                    break
                heartbeat_count += 1
                elapsed = int(time.time() - start_time)
                if self.ctx:
                    try:
                        await self.ctx.report_progress(
                            progress=heartbeat_count,
                            total=None,
                            message=f"⏳ Still working... ({elapsed}s elapsed)",
                        )
                    except Exception as e:
                        logger.debug(f"Heartbeat progress report failed: {e}")

        # Start heartbeat in background
        heartbeat = asyncio.create_task(heartbeat_task())

        try:
            async for event in self.parser.parse_events():
                last_activity = time.time()

                logger.info(f"[InteractiveExecutor] ⚡ RESUMED EVENT: {event.type}")

                # Log full message content for ASSISTANT events to see tool uses
                if event.type == ClaudeEventType.ASSISTANT and "message" in event.data:
                    message = event.data.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content", [])
                        logger.info(f"[InteractiveExecutor] 📦 RESUMED Message content blocks: {len(content) if isinstance(content, list) else 0}")
                        if isinstance(content, list):
                            for i, block in enumerate(content):
                                if isinstance(block, dict):
                                    block_type = block.get("type", "unknown")
                                    logger.info(f"[InteractiveExecutor]   RESUMED Block {i}: type={block_type}")
                                    if block_type == "tool_use":
                                        logger.info(f"[InteractiveExecutor]     TOOL USE: {block.get('name', 'unknown')}")
                                        logger.info(f"[InteractiveExecutor]     Input: {block.get('input', {})}")
                                    elif block_type == "text":
                                        text = block.get("text", "")
                                        logger.info(f"[InteractiveExecutor]     Text: {text[:200]}")

                # Extract session_id if present
                if "session_id" in event.data:
                    self.session_id = event.data["session_id"]

                # Update progress
                await self._report_progress(event)

                # Check for more interactions
                interaction_response = await self.interaction_handler.handle_event(event)
                if interaction_response:
                    logger.info(f"[InteractiveExecutor] 💬 ANOTHER INTERACTION: type={interaction_response['type']}")

                    # Update metrics
                    self._update_metrics(interaction_response["type"])

                    # Recursively resume again
                    if self.session_id:
                        logger.info(f"[InteractiveExecutor] 🔄 RESUMING AGAIN with session {self.session_id}")
                        await self._terminate_process()
                        # Format response with context for better Claude understanding
                        formatted_response = self._format_response_with_context(interaction_response)
                        result = await self._resume_session(formatted_response)
                        if result:
                            return result

                # Accumulate output
                text_content = extract_text_content(event)
                if text_content:
                    output_buffer.append(text_content)
                    logger.info(f"[InteractiveExecutor] 📝 Resumed output: {text_content[:500]}")

                # Check for completion
                if event.type == ClaudeEventType.RESULT:
                    logger.info("[InteractiveExecutor] ✅ RESUMED session completed")
                    result = parse_result_event(event)

                    # Use output_buffer if result output is empty
                    if not result.get("output") and output_buffer:
                        result["output"] = "\n".join(output_buffer)

                    result.update(
                        {
                            "permissions_requested": self.permissions_requested,
                            "permissions_granted": self.permissions_granted,
                            "choices_asked": self.choices_asked,
                            "questions_asked": self.questions_asked,
                            "confirmations_asked": self.confirmations_asked,
                        }
                    )
                    return result

        finally:
            # Stop heartbeat when done
            heartbeat_running = False
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        # Process ended without result
        returncode = await self.process.wait()
        stderr = await self.process.stderr.read()

        return {
            "success": returncode == 0,
            "output": "\n".join(output_buffer),
            "error": stderr.decode() if stderr else None,
            "returncode": returncode,
            "permissions_requested": self.permissions_requested,
            "permissions_granted": self.permissions_granted,
            "choices_asked": self.choices_asked,
            "questions_asked": self.questions_asked,
            "confirmations_asked": self.confirmations_asked,
        }

    async def _terminate_process(self) -> None:
        """Gracefully terminate the process."""
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()

    async def _setup_permission_server(self) -> str:
        """Setup embedded permission server for native permission handling.

        Creates:
        1. Unix socket callback server in main process for elicitation
        2. MCP config file pointing to approver.py subprocess

        Returns:
            Path to generated MCP config file
        """

        async def elicitation_callback(tool_name: str, tool_input: dict) -> dict:
            """Called when embedded server receives permission request.

            Args:
                tool_name: Name of tool requesting permission
                tool_input: Tool input parameters

            Returns:
                Dict with 'granted' bool and optional 'message' or 'decision'
            """
            # Format target from tool_input for display and caching
            target = self._format_permission_target(tool_name, tool_input)

            logger.info(f"[InteractiveExecutor] 🔐 Permission callback: {tool_name} on {target}")
            logger.debug(f"[InteractiveExecutor] 🔐 Raw tool_input: {tool_input}")

            # Check cached permissions first
            existing = self.permission_manager.check_permission(
                action=tool_name,
                target=target,
            )
            if existing:
                logger.info(f"[InteractiveExecutor] ✅ CACHED permission found: {existing.decision.value}")
                logger.info(f"[InteractiveExecutor] ✅ Auto-granting (no dialog shown)")
                self.permissions_requested += 1
                self.permissions_granted += 1
                return {"granted": True, "decision": existing.decision.value}

            # Show elicitation dialog to user
            message = f"Allow {tool_name} on {target}?"
            logger.info(f"[InteractiveExecutor] 🔔 SHOWING PERMISSION DIALOG: {message}")
            logger.info(f"[InteractiveExecutor] 🔔 This is permission request #{self.permissions_requested + 1}")

            result = await self.ctx.elicit(
                message,
                response_type=["Allow Once", "Allow Session", "Allow Always", "Deny"],
            )

            self.permissions_requested += 1
            logger.info(f"[InteractiveExecutor] 🔔 User responded: action={result.action}, data={result.data}")

            if result.action != "accept" or result.data == "Deny":
                logger.info(f"[InteractiveExecutor] ❌ Permission DENIED by user")
                return {"granted": False, "message": "Permission denied by user"}

            self.permissions_granted += 1
            decision = result.data
            logger.info(f"[InteractiveExecutor] ✅ Permission GRANTED: {decision}")

            # Store permission if needed
            if decision == "Allow Session":
                self.permission_manager.store_permission(
                    action=tool_name,
                    target=target,
                    decision=PermissionDecision.ALLOW_SESSION,
                )
            elif decision == "Allow Always":
                self.permission_manager.store_permission(
                    action=tool_name,
                    target=target,
                    decision=PermissionDecision.ALLOW_ALWAYS,
                )

            return {"granted": True, "decision": decision}

        # Start callback server (Unix socket)
        self.callback_server = ElicitationCallbackServer(
            elicitation_callback=elicitation_callback,
            permission_manager=self.permission_manager,
        )
        socket_path = await self.callback_server.start()
        logger.info(f"[InteractiveExecutor] Callback server started at {socket_path}")

        # Generate MCP config for Claude Code
        approver_script = str(
            Path(__file__).parent.parent / "permission_server" / "approver.py"
        )

        mcp_config = {
            "mcpServers": {
                "perm": {
                    "command": sys.executable,
                    "args": [approver_script, socket_path],
                }
            }
        }

        config_path = Path(tempfile.gettempdir()) / f"mcp-config-{id(self)}.json"
        config_path.write_text(json.dumps(mcp_config))
        self.approver_config_path = str(config_path)

        logger.info(f"[InteractiveExecutor] MCP config written to {self.approver_config_path}")
        return self.approver_config_path

    async def _cleanup_permission_server(self) -> None:
        """Cleanup native permission server resources."""
        if self.callback_server:
            try:
                await self.callback_server.stop()
                logger.info("[InteractiveExecutor] Callback server stopped")
            except Exception as e:
                logger.warning(f"[InteractiveExecutor] Error stopping callback server: {e}")
            self.callback_server = None

        if self.approver_config_path:
            try:
                Path(self.approver_config_path).unlink(missing_ok=True)
                logger.info(f"[InteractiveExecutor] Removed MCP config: {self.approver_config_path}")
            except Exception as e:
                logger.warning(f"[InteractiveExecutor] Error removing config file: {e}")
            self.approver_config_path = None

    def _format_response_with_context(self, interaction_response: dict) -> str:
        """Format user response with context for better Claude understanding.

        When resuming a session, Claude needs to understand that the user's
        response is an answer to its previous question, not a new command.

        Args:
            interaction_response: Dict with type, text, and optionally question_text

        Returns:
            Formatted response string with context
        """
        response_type = interaction_response.get("type", "")
        text = interaction_response.get("text", "")
        question_text = interaction_response.get("question_text", "")

        if response_type == "question" and question_text:
            # Add context so Claude understands this is an answer to its question
            # Using a clear format that Claude can recognize
            return f'В ответ на вопрос "{question_text}": {text}'
        elif response_type == "choice":
            # Choice responses already have "I choose:" prefix
            return text
        elif response_type == "confirmation":
            # Confirmation responses already have "CONFIRMED:" prefix
            return text

        # Fallback: return as-is
        return text

    def _format_permission_target(self, tool_name: str, tool_input: dict) -> str:
        """Extract meaningful target from tool input for display and caching.

        Normalizes paths to ensure consistent caching across requests.

        Args:
            tool_name: Name of tool
            tool_input: Tool input parameters

        Returns:
            Human-readable target string (normalized for paths)
        """
        if tool_name in ("Read", "Write", "Edit"):
            path = tool_input.get("file_path", "")
            if path:
                # Normalize path for consistent caching
                try:
                    normalized = str(Path(path).resolve())
                    logger.debug(f"[InteractiveExecutor] Normalized path: {path} -> {normalized}")
                    return normalized
                except Exception:
                    return path
            return str(tool_input)
        elif tool_name == "Bash":
            cmd = tool_input.get("command", str(tool_input))
            # Normalize command by stripping whitespace
            cmd = cmd.strip()
            return cmd[:100] if len(cmd) > 100 else cmd
        elif tool_name == "Glob":
            pattern = tool_input.get("pattern", str(tool_input))
            path = tool_input.get("path", "")
            if path:
                try:
                    normalized_path = str(Path(path).resolve())
                    return f"{pattern} in {normalized_path}"
                except Exception:
                    return f"{pattern} in {path}"
            return pattern
        elif tool_name == "Grep":
            pattern = tool_input.get("pattern", "")
            path = tool_input.get("path", ".")
            if path:
                try:
                    normalized_path = str(Path(path).resolve())
                    return f"{pattern} in {normalized_path}"
                except Exception:
                    return f"{pattern} in {path}"
            return f"{pattern} in ."
        elif tool_name == "WebFetch":
            return tool_input.get("url", str(tool_input))
        elif tool_name == "WebSearch":
            return tool_input.get("query", str(tool_input))

        # Default: truncated string representation
        target = str(tool_input)
        return target[:100] if len(target) > 100 else target
