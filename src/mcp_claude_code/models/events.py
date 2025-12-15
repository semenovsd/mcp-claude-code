"""Event models for Claude Code CLI stream-json output."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaudeEventType(Enum):
    """Types of events from Claude Code CLI."""

    INIT = "init"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    UNKNOWN = "unknown"


@dataclass
class ContentBlock:
    """A content block within a message.

    Attributes:
        type: Block type ("text", "tool_use", "tool_result", "thinking")
        text: Text content (for text blocks)
        id: Tool use ID (for tool_use blocks)
        name: Tool name (for tool_use blocks)
        input: Tool input parameters (for tool_use blocks)
        tool_use_id: Reference to tool use (for tool_result blocks)
    """

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentBlock":
        """Create ContentBlock from dictionary.

        Args:
            data: Dictionary with content block data

        Returns:
            Parsed ContentBlock
        """
        return cls(
            type=data.get("type", ""),
            text=data.get("text"),
            id=data.get("id"),
            name=data.get("name"),
            input=data.get("input"),
            tool_use_id=data.get("tool_use_id"),
        )


@dataclass
class Message:
    """A message with content blocks.

    Attributes:
        role: Message role ("assistant" or "user")
        content: List of content blocks
    """

    role: str
    content: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Create Message from dictionary.

        Args:
            data: Dictionary with message data

        Returns:
            Parsed Message
        """
        content = [
            ContentBlock.from_dict(c)
            for c in data.get("content", [])
            if isinstance(c, dict)
        ]
        return cls(
            role=data.get("role", ""),
            content=content,
        )


@dataclass
class ClaudeEvent:
    """Parsed event from Claude Code stream.

    Attributes:
        type: Type of event (init, assistant, tool_use, etc.)
        data: Raw event data dictionary
        raw_line: Original NDJSON line
        message: Parsed message (for assistant/user events)
    """

    type: ClaudeEventType
    data: dict[str, Any]
    raw_line: str
    message: Message | None = None

    @classmethod
    def from_json_line(cls, line: str) -> "ClaudeEvent":
        """Parse a single NDJSON line into an event.

        Args:
            line: JSON string from Claude Code output

        Returns:
            Parsed ClaudeEvent

        Examples:
            >>> event = ClaudeEvent.from_json_line('{"type":"init","session_id":"abc"}')
            >>> event.type
            <ClaudeEventType.INIT: 'init'>
        """
        import json

        try:
            data = json.loads(line)
            event_type = ClaudeEventType(data.get("type", "unknown"))
        except (json.JSONDecodeError, ValueError):
            event_type = ClaudeEventType.UNKNOWN
            data = {"raw": line}

        # Parse message if present
        message = None
        if "message" in data and isinstance(data["message"], dict):
            message = Message.from_dict(data["message"])

        return cls(type=event_type, data=data, raw_line=line, message=message)

    def get_text_content(self) -> str:
        """Extract text content from message.

        Returns:
            Concatenated text from all text content blocks

        Examples:
            >>> event = ClaudeEvent(...)
            >>> event.get_text_content()
            'Hello, I will help you...'
        """
        if not self.message:
            return ""
        texts = []
        for block in self.message.content:
            if block.type == "text" and block.text:
                texts.append(block.text)
        return "".join(texts)

    def get_tool_uses(self) -> list[ContentBlock]:
        """Extract tool_use blocks from message.

        Returns:
            List of ContentBlock objects with type="tool_use"

        Examples:
            >>> event = ClaudeEvent(...)
            >>> tool_uses = event.get_tool_uses()
            >>> tool_uses[0].name
            'Read'
        """
        if not self.message:
            return []
        return [b for b in self.message.content if b.type == "tool_use"]


@dataclass
class ResultEvent:
    """Final result event from Claude Code execution.

    Attributes:
        success: Whether execution was successful
        output: Main output text
        total_cost_usd: Total API cost
        duration_ms: Execution duration in milliseconds
        num_turns: Number of conversation turns
        error_message: Error message if failed
    """

    success: bool
    output: str
    total_cost_usd: float
    duration_ms: int
    num_turns: int
    error_message: str | None = None
