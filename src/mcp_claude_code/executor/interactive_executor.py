"""Interactive executor for Claude Code CLI."""

import asyncio
import json
import logging
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models.events import ClaudeEvent, ClaudeEventType
from ..models.interactions import PermissionDecision, PermissionResponse
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


@dataclass
class EventLoopResult:
    """Result from event loop processing."""

    is_complete: bool
    """True if execution completed (RESULT event received)."""

    result: dict[str, Any] | None = None
    """Final result dictionary if complete."""

    pending_resume_response: str | None = None
    """User response pending for session resumption."""

    output_buffer: list[str] = field(default_factory=list)
    """Accumulated output from Claude."""


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
                    self._run_event_loop(inactivity_timeout_seconds),
                    timeout=max_execution_seconds,
                )
                return result
            except asyncio.TimeoutError:
                await self._terminate_process()
                return self._build_error_result(
                    f"Execution exceeded {max_execution_seconds}s timeout"
                )

        finally:
            # Cleanup native permission server resources
            await self._cleanup_permission_server()

    async def _run_event_loop(self, inactivity_timeout: int) -> dict[str, Any]:
        """Main event processing loop with resumption support.

        Args:
            inactivity_timeout: Seconds of inactivity before timeout

        Returns:
            Execution result dictionary
        """
        loop_result = await self._process_events(inactivity_timeout)

        # Handle resumption if needed
        if loop_result.pending_resume_response and self.session_id:
            logger.info(f"[InteractiveExecutor] 🔄 Event loop ended, resuming with session {self.session_id}")

            # Terminate current process before resuming
            if self.process and self.process.returncode is None:
                logger.info("[InteractiveExecutor] 🛑 Terminating current process...")
                await self._terminate_process()
            else:
                logger.info(f"[InteractiveExecutor] Process already exited (returncode={self.process.returncode if self.process else 'None'})")

            # Resume with the user's response
            return await self._resume_session(loop_result.pending_resume_response)

        # Return result or build from process exit
        if loop_result.result:
            return loop_result.result

        # Process ended without RESULT event
        returncode = await self.process.wait()
        stderr = await self.process.stderr.read()

        return {
            "success": returncode == 0,
            "output": "\n".join(loop_result.output_buffer),
            "error": stderr.decode() if stderr else None,
            "returncode": returncode,
            **self._get_metrics(),
        }

    async def _process_events(
        self,
        inactivity_timeout: int,
        is_resumed: bool = False,
    ) -> EventLoopResult:
        """Process events from Claude Code CLI stream.

        This is the unified event processing logic used by both initial
        execution and session resumption.

        Args:
            inactivity_timeout: Seconds of inactivity before timeout
            is_resumed: True if this is a resumed session

        Returns:
            EventLoopResult with processing outcome
        """
        output_buffer: list[str] = []
        last_activity = time.time()
        pending_resume_response: str | None = None
        start_time = time.time()
        log_prefix = "[InteractiveExecutor] RESUMED" if is_resumed else "[InteractiveExecutor]"

        # Heartbeat mechanism - sends progress every 5 seconds
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(start_time)
        )

        try:
            async for event in self.parser.parse_events():
                last_activity = time.time()

                logger.info(f"{log_prefix} ⚡ EVENT RECEIVED: {event.type}")
                self._log_event_details(event, log_prefix)

                # Extract session_id if present
                if "session_id" in event.data:
                    self.session_id = event.data["session_id"]
                    logger.info(f"{log_prefix} 📋 SESSION_ID captured: {self.session_id}")

                # Update progress
                await self._report_progress(event)

                # Handle interactions
                interaction_response = await self.interaction_handler.handle_event(event)
                if interaction_response:
                    logger.info(
                        f"{log_prefix} 💬 INTERACTION HANDLED: "
                        f"type={interaction_response['type']}, text={interaction_response['text'][:100]}"
                    )

                    # Update metrics
                    self._update_metrics(interaction_response["type"])

                    # For multi-turn: prepare to resume
                    if self.session_id:
                        pending_resume_response = self._format_response_with_context(interaction_response)
                        logger.info(f"{log_prefix} 💾 Saved response for resume: {pending_resume_response[:100]}")
                    else:
                        logger.warning(f"{log_prefix} ⚠️ No session_id - falling back to stdin")
                        await self._send_stdin_message(interaction_response["text"])

                # Accumulate output
                text_content = extract_text_content(event)
                if text_content:
                    output_buffer.append(text_content)
                    logger.info(f"{log_prefix} 📝 Claude output: {text_content[:500]}")

                # Check for completion
                if event.type == ClaudeEventType.RESULT:
                    logger.info(f"{log_prefix} ✅ RESULT event received")

                    # Need to resume?
                    if pending_resume_response and self.session_id:
                        logger.info(f"{log_prefix} 🔄 RESULT received but need to resume")
                        return EventLoopResult(
                            is_complete=False,
                            pending_resume_response=pending_resume_response,
                            output_buffer=output_buffer,
                        )

                    # Complete - return result
                    result = parse_result_event(event)
                    if not result.get("output") and output_buffer:
                        result["output"] = "\n".join(output_buffer)

                    result.update(self._get_metrics())
                    return EventLoopResult(
                        is_complete=True,
                        result=result,
                        output_buffer=output_buffer,
                    )

                # Check inactivity timeout
                if time.time() - last_activity > inactivity_timeout:
                    logger.warning(f"{log_prefix} ⏰ Inactivity timeout after {inactivity_timeout}s")
                    await self._terminate_process()
                    return EventLoopResult(
                        is_complete=True,
                        result=self._build_error_result(
                            f"Inactivity timeout after {inactivity_timeout}s",
                            output="\n".join(output_buffer),
                        ),
                        output_buffer=output_buffer,
                    )

        finally:
            # Stop heartbeat
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # Stream ended - check if resumption needed
        return EventLoopResult(
            is_complete=False,
            pending_resume_response=pending_resume_response,
            output_buffer=output_buffer,
        )

    async def _heartbeat_loop(self, start_time: float) -> None:
        """Send periodic heartbeat progress messages.

        Args:
            start_time: When execution started
        """
        heartbeat_interval = 5
        heartbeat_count = 0

        while True:
            await asyncio.sleep(heartbeat_interval)
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

    def _log_event_details(self, event: ClaudeEvent, log_prefix: str) -> None:
        """Log detailed event information for debugging.

        Args:
            event: Claude event to log
            log_prefix: Prefix for log messages
        """
        logger.debug(f"{log_prefix} Event data keys: {list(event.data.keys()) if hasattr(event, 'data') else 'NO DATA'}")

        if event.type == ClaudeEventType.ASSISTANT and "message" in event.data:
            message = event.data.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", [])
                logger.info(f"{log_prefix} 📦 Message content blocks: {len(content) if isinstance(content, list) else 0}")
                if isinstance(content, list):
                    for i, block in enumerate(content):
                        if isinstance(block, dict):
                            block_type = block.get("type", "unknown")
                            logger.info(f"{log_prefix}   Block {i}: type={block_type}")
                            if block_type == "tool_use":
                                logger.info(f"{log_prefix}     Tool: {block.get('name', 'unknown')}")
                                logger.info(f"{log_prefix}     Input: {block.get('input', {})}")
                            elif block_type == "text":
                                text = block.get("text", "")
                                logger.info(f"{log_prefix}     Text: {text[:200]}")

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

    def _get_metrics(self) -> dict[str, int]:
        """Get current execution metrics.

        Returns:
            Dictionary with all metric counts
        """
        return {
            "permissions_requested": self.permissions_requested,
            "permissions_granted": self.permissions_granted,
            "choices_asked": self.choices_asked,
            "questions_asked": self.questions_asked,
            "confirmations_asked": self.confirmations_asked,
        }

    def _build_error_result(
        self,
        error: str,
        output: str = "",
        returncode: int = -1,
    ) -> dict[str, Any]:
        """Build error result dictionary.

        Args:
            error: Error message
            output: Any output collected
            returncode: Process return code

        Returns:
            Error result dictionary with metrics
        """
        return {
            "success": False,
            "error": error,
            "output": output,
            "returncode": returncode,
            **self._get_metrics(),
        }

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

    async def _resume_session(self, user_response: str) -> dict[str, Any]:
        """Resume session with user's response.

        Args:
            user_response: User's answer to send to Claude

        Returns:
            Execution result dictionary
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

        # Process events from resumed session
        loop_result = await self._process_events(
            inactivity_timeout=self.settings.inactivity_timeout_seconds,
            is_resumed=True,
        )

        # Handle recursive resumption
        if loop_result.pending_resume_response and self.session_id:
            logger.info(f"[InteractiveExecutor] 🔄 RESUMING AGAIN with session {self.session_id}")
            await self._terminate_process()
            return await self._resume_session(loop_result.pending_resume_response)

        # Return result
        if loop_result.result:
            return loop_result.result

        # Process ended without RESULT event
        returncode = await self.process.wait()
        stderr = await self.process.stderr.read()

        return {
            "success": returncode == 0,
            "output": "\n".join(loop_result.output_buffer),
            "error": stderr.decode() if stderr else None,
            "returncode": returncode,
            **self._get_metrics(),
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
                logger.info("[InteractiveExecutor] ✅ Auto-granting (no dialog shown)")
                self.permissions_requested += 1
                self.permissions_granted += 1
                return {"granted": True, "decision": existing.decision.value}

            # Show elicitation dialog to user
            message = f"Allow {tool_name} on {target}?"
            logger.info(f"[InteractiveExecutor] 🔔 SHOWING PERMISSION DIALOG: {message}")
            logger.info(f"[InteractiveExecutor] 🔔 This is permission request #{self.permissions_requested + 1}")

            # Use centralized permission options from enum
            result = await self.ctx.elicit(
                message,
                response_type=PermissionResponse.all_options(),
            )

            self.permissions_requested += 1
            logger.info(f"[InteractiveExecutor] 🔔 User responded: action={result.action}, data={result.data}")

            if result.action != "accept" or result.data == PermissionResponse.DENY.value:
                logger.info("[InteractiveExecutor] ❌ Permission DENIED by user")
                return {"granted": False, "message": "Permission denied by user"}

            self.permissions_granted += 1
            decision_str = result.data
            logger.info(f"[InteractiveExecutor] ✅ Permission GRANTED: {decision_str}")

            # Convert string response to enum and store if needed
            try:
                response = PermissionResponse.from_string(decision_str)
                decision = response.to_decision()

                if decision in (PermissionDecision.ALLOW_SESSION, PermissionDecision.ALLOW_ALWAYS):
                    self.permission_manager.store_permission(
                        action=tool_name,
                        target=target,
                        decision=decision,
                    )
            except ValueError as e:
                logger.warning(f"[InteractiveExecutor] Unknown permission response: {e}")

            return {"granted": True, "decision": decision_str}

        # Start callback server (Unix socket)
        self.callback_server = ElicitationCallbackServer(
            elicitation_callback=elicitation_callback,
            permission_manager=self.permission_manager,
        )
        socket_path = await self.callback_server.start()
        logger.info(f"[InteractiveExecutor] Callback server started at {socket_path}")

        # Generate MCP config for Claude Code with settings
        approver_script = str(
            Path(__file__).parent.parent / "permission_server" / "approver.py"
        )

        # Pass timeout and retry settings to approver subprocess
        mcp_config = {
            "mcpServers": {
                "perm": {
                    "command": sys.executable,
                    "args": [
                        approver_script,
                        socket_path,
                        "--timeout", str(self.settings.permission_timeout_seconds),
                        "--retries", str(self.settings.socket_retry_attempts),
                        "--retry-delay", str(self.settings.socket_retry_delay_seconds),
                    ],
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
        # Strategy pattern for target formatting
        formatters = {
            "Read": lambda i: self._normalize_path(i.get("file_path", "")),
            "Write": lambda i: self._normalize_path(i.get("file_path", "")),
            "Edit": lambda i: self._normalize_path(i.get("file_path", "")),
            "Bash": lambda i: i.get("command", str(i)).strip()[:100],
            "Glob": lambda i: self._format_glob_target(i),
            "Grep": lambda i: self._format_grep_target(i),
            "WebFetch": lambda i: i.get("url", str(i)),
            "WebSearch": lambda i: i.get("query", str(i)),
        }

        formatter = formatters.get(tool_name)
        if formatter:
            result = formatter(tool_input)
            if result:
                return result

        # Default: truncated string representation
        target = str(tool_input)
        return target[:100] if len(target) > 100 else target

    def _normalize_path(self, path: str) -> str:
        """Normalize file path for consistent caching.

        Args:
            path: File path to normalize

        Returns:
            Normalized absolute path, or original if normalization fails
        """
        if not path:
            return ""
        try:
            normalized = str(Path(path).resolve())
            logger.debug(f"[InteractiveExecutor] Normalized path: {path} -> {normalized}")
            return normalized
        except Exception:
            return path

    def _format_glob_target(self, tool_input: dict) -> str:
        """Format Glob tool target.

        Args:
            tool_input: Glob tool input

        Returns:
            Formatted target string
        """
        pattern = tool_input.get("pattern", str(tool_input))
        path = tool_input.get("path", "")
        if path:
            normalized_path = self._normalize_path(path)
            return f"{pattern} in {normalized_path}"
        return pattern

    def _format_grep_target(self, tool_input: dict) -> str:
        """Format Grep tool target.

        Args:
            tool_input: Grep tool input

        Returns:
            Formatted target string
        """
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", ".")
        if path:
            normalized_path = self._normalize_path(path)
            return f"{pattern} in {normalized_path}"
        return f"{pattern} in ."
