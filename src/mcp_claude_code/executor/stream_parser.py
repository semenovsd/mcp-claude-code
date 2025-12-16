"""Stream parser for Claude Code CLI NDJSON output."""

import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from ..models.events import ClaudeEvent, ClaudeEventType

logger = logging.getLogger(__name__)


# Tool emoji mapping for progress messages
TOOL_EMOJIS = {
    "Read": "📖",
    "Edit": "✏️",
    "Write": "📝",
    "Bash": "💻",
    "Glob": "🔍",
    "Grep": "🔎",
    "WebFetch": "🌐",
    "WebSearch": "🔍",
    "Task": "📋",
    "TodoWrite": "📝",
    "NotebookEdit": "📓",
    "AskUserQuestion": "❓",
    "EnterPlanMode": "📝",
    "ExitPlanMode": "✅",
    "mcp__ide__getDiagnostics": "🔬",
    "mcp__ide__executeCode": "🐍",
}


class StreamParser:
    """Parses NDJSON stream from Claude Code CLI.

    Reads lines from stdout asyncio stream, parses each line as JSON,
    and yields structured ClaudeEvent objects.

    Attributes:
        stdout_stream: Asyncio StreamReader from subprocess stdout
    """

    def __init__(self, stdout_stream: Any) -> None:
        """Initialize parser.

        Args:
            stdout_stream: Asyncio StreamReader from subprocess
        """
        self.stdout_stream = stdout_stream

    async def parse_events(self) -> AsyncIterator[ClaudeEvent]:
        """Async generator that yields parsed events.

        Yields:
            ClaudeEvent objects as they arrive from stream

        Examples:
            >>> async for event in parser.parse_events():
            ...     if event.type == ClaudeEventType.RESULT:
            ...         print("Done!")
        """
        async for line in self._read_lines():
            line = line.strip()

            if not line:
                continue

            event = ClaudeEvent.from_json_line(line)
            yield event

    async def _read_lines(self) -> AsyncIterator[str]:
        """Read lines from stdout stream.

        Yields:
            Decoded string lines from stream
        """
        while True:
            try:
                line = await self.stdout_stream.readline()
                if not line:
                    break

                yield line.decode("utf-8", errors="replace")
            except Exception as e:
                # Log error but continue (graceful degradation)
                logger.error(f"StreamParser error reading from stdout: {e}")
                break


def extract_text_content(event: ClaudeEvent) -> str:
    """Extract text content from assistant event.

    Args:
        event: Claude event with content blocks

    Returns:
        Concatenated text from all text content blocks

    Examples:
        >>> event = ClaudeEvent(...)
        >>> text = extract_text_content(event)
        >>> "permission request" in text
        True
    """
    # Use new method if available
    if hasattr(event, 'get_text_content'):
        return event.get_text_content()

    # Fallback: Try to get content from message.content first (Claude Code CLI format)
    message = event.data.get("message", {})
    if isinstance(message, dict):
        content = message.get("content", [])
    else:
        # Fallback to direct content field
        content = event.data.get("content", [])

    if not isinstance(content, list):
        return ""

    text_parts = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            text_parts.append(c.get("text", ""))

    return "\n".join(text_parts)


def parse_result_event(event: ClaudeEvent) -> dict[str, Any]:
    """Parse result event into structured dict.

    Args:
        event: Result event from Claude Code

    Returns:
        Dictionary with success, output, cost, duration, etc.

    Examples:
        >>> event = ClaudeEvent(type=ClaudeEventType.RESULT, data={...})
        >>> result = parse_result_event(event)
        >>> result["success"]
        True
    """
    data = event.data
    return {
        "success": data.get("subtype") == "success",
        "output": data.get("output", ""),
        "total_cost_usd": data.get("total_cost_usd", 0.0),
        "duration_ms": data.get("duration_ms", 0),
        "num_turns": data.get("num_turns", 0),
        "error_message": data.get("error_message"),
    }


def _truncate_path(path: str, max_len: int = 35) -> str:
    """Truncate path showing filename and parent directory.

    Args:
        path: Full file path
        max_len: Maximum length of output

    Returns:
        Truncated path like "...parent/filename.py"
    """
    if not path:
        return ""
    if len(path) <= max_len:
        return path
    # Show: ...parent/filename
    parts = path.split("/")
    if len(parts) >= 2:
        short = f"...{parts[-2]}/{parts[-1]}"
        if len(short) <= max_len:
            return short
    return "..." + path[-(max_len - 3):]


def _extract_tool_detail(tool_name: str, input_data: dict[str, Any] | None) -> str | None:
    """Extract human-readable detail from tool input.

    Args:
        tool_name: Name of the tool
        input_data: Tool input parameters

    Returns:
        Human-readable detail string or None

    Examples:
        >>> _extract_tool_detail("Read", {"file_path": "/home/user/main.py"})
        'main.py'
        >>> _extract_tool_detail("Bash", {"command": "npm install"})
        'npm install'
    """
    if not input_data:
        return None

    if tool_name == "Read":
        path = input_data.get("file_path", "")
        return _truncate_path(path) if path else None

    elif tool_name == "Edit":
        path = input_data.get("file_path", "")
        return _truncate_path(path) if path else None

    elif tool_name == "Write":
        path = input_data.get("file_path", "")
        return _truncate_path(path) if path else None

    elif tool_name == "Bash":
        cmd = input_data.get("command", "")
        if cmd:
            # Truncate long commands
            return cmd[:45] + "..." if len(cmd) > 45 else cmd
        return None

    elif tool_name == "Glob":
        pattern = input_data.get("pattern", "")
        path = input_data.get("path", "")
        if pattern:
            if path:
                return f"{pattern} in {_truncate_path(path)}"
            return pattern
        return None

    elif tool_name == "Grep":
        pattern = input_data.get("pattern", "")
        path = input_data.get("path", "")
        if pattern:
            if path:
                return f"`{pattern}` in {_truncate_path(path)}"
            return f"`{pattern}`"
        return None

    elif tool_name == "WebFetch":
        url = input_data.get("url", "")
        if url:
            try:
                domain = urlparse(url).netloc
                return domain[:35] if domain else url[:35]
            except Exception:
                return url[:35]
        return None

    elif tool_name == "WebSearch":
        query = input_data.get("query", "")
        return f'"{query[:40]}"' if query else None

    elif tool_name == "Task":
        desc = input_data.get("description", "")
        return f'"{desc}"' if desc else None

    elif tool_name == "TodoWrite":
        todos = input_data.get("todos", [])
        if todos and isinstance(todos, list):
            count = len(todos)
            return f"{count} item{'s' if count != 1 else ''}"
        return None

    return None


def _is_interaction_marker(text: str) -> bool:
    """Check if text starts with a JSON interaction marker.

    Args:
        text: Text to check

    Returns:
        True if text is an interaction marker that should not be shown as progress
    """
    stripped = text.strip()
    return (
        stripped.startswith('{"__user_question__":')
        or stripped.startswith('{"__user_choice__":')
        or stripped.startswith('{"__confirmation__":')
    )


def _is_permission_tool(tool_name: str) -> bool:
    """Check if the tool is a permission-related tool.

    Permission tools are handled separately via elicitation callback,
    so they should not be shown in progress messages.

    Args:
        tool_name: Name of the tool

    Returns:
        True if this is a permission-related tool
    """
    if not tool_name:
        return False
    name_lower = tool_name.lower()
    return "approve" in name_lower or "perm" in name_lower


def format_progress_message(event: ClaudeEvent) -> str:
    """Format a ClaudeEvent into a human-readable progress message.

    Produces informative messages with emoji indicators showing what
    Claude is currently doing. Filters out JSON interaction markers
    to avoid confusing the client.

    Args:
        event: The ClaudeEvent to format

    Returns:
        A short progress message string with emoji indicators

    Examples:
        >>> event = ClaudeEvent(type=ClaudeEventType.ASSISTANT, ...)
        >>> format_progress_message(event)
        '📖 Read: src/main.py | 💻 Bash: npm install'
    """
    if event.type == ClaudeEventType.ASSISTANT:
        # Check for tool use
        tool_uses = event.get_tool_uses()
        if tool_uses:
            messages = []
            for tu in tool_uses:
                name = tu.name or "unknown"
                input_data = tu.input or {}

                # Skip permission-related tools - they are handled via elicitation
                if _is_permission_tool(name):
                    continue

                # Handle MCP tool names (mcp__server__tool)
                display_name = name.split("__")[-1] if "__" in name else name
                emoji = TOOL_EMOJIS.get(name, TOOL_EMOJIS.get(display_name, "🔧"))

                # Extract meaningful detail from tool input
                detail = _extract_tool_detail(name, input_data)

                if detail:
                    messages.append(f"{emoji} {display_name}: {detail}")
                else:
                    messages.append(f"{emoji} {display_name}")

            # If all tools were permission-related, show waiting message
            if not messages:
                return "🔐 Awaiting permission decision..."

            # Multiple tools separated by |
            return " | ".join(messages)

        # Check for text content
        text = event.get_text_content()
        if text:
            # Filter out JSON interaction markers - they should not be shown as progress
            # These are handled separately via elicitation
            if _is_interaction_marker(text):
                return "⏳ Awaiting user input..."

            # Truncate to first 55 chars, remove newlines
            preview = text[:55].replace("\n", " ").strip()
            if len(text) > 55:
                preview += "..."
            return f"💭 {preview}"

        return "🤔 Claude is thinking..."

    elif event.type == ClaudeEventType.USER:
        # Tool result
        return "⚙️ Processing tool result..."

    elif event.type == ClaudeEventType.RESULT:
        # Parse result data
        data = event.data
        success = data.get("subtype") == "success"
        duration_ms = data.get("duration_ms", 0)
        cost = data.get("total_cost_usd", 0)

        if success:
            cost_str = f", cost: ${cost:.4f}" if cost > 0 else ""
            return f"✅ Completed in {duration_ms}ms{cost_str}"

        error = data.get("result", "")
        error_preview = error[:45] + "..." if error and len(error) > 45 else (error or "Unknown error")
        return f"❌ Failed: {error_preview}"

    elif event.type == ClaudeEventType.INIT:
        return "🚀 Starting Claude Code..."

    elif event.type == ClaudeEventType.TOOL_USE:
        return "🔧 Executing tool..."

    elif event.type == ClaudeEventType.TOOL_RESULT:
        return "📥 Received tool result..."

    return "⏳ Processing..."
