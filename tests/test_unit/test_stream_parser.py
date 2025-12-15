"""Unit tests for stream_parser formatting functions.

These test PURE functions - no mocks needed!
Testing actual formatting logic with real data.
"""

import pytest

from mcp_claude_code.executor.stream_parser import (
    _extract_tool_detail,
    _truncate_path,
    format_progress_message,
    TOOL_EMOJIS,
)
from mcp_claude_code.models.events import (
    ClaudeEvent,
    ClaudeEventType,
    ContentBlock,
    Message,
)


class TestTruncatePath:
    """Test _truncate_path function."""

    def test_short_path_unchanged(self):
        """Short paths should not be truncated."""
        path = "src/main.py"
        result = _truncate_path(path, max_len=35)
        assert result == path

    def test_long_path_truncated(self):
        """Long paths should be truncated showing parent/filename."""
        path = "/home/user/projects/myproject/src/components/Header.tsx"
        result = _truncate_path(path, max_len=35)
        assert "..." in result
        assert "Header.tsx" in result

    def test_empty_path(self):
        """Empty path returns empty string."""
        assert _truncate_path("") == ""

    def test_path_shows_parent(self):
        """Truncated path shows parent directory and filename."""
        path = "/very/long/path/to/some/deeply/nested/file.py"
        result = _truncate_path(path, max_len=35)
        assert "nested/file.py" in result


class TestExtractToolDetail:
    """Test _extract_tool_detail function."""

    def test_read_tool_shows_file_path(self):
        """Read tool shows file path."""
        detail = _extract_tool_detail("Read", {"file_path": "/src/main.py"})
        assert "main.py" in detail

    def test_edit_tool_shows_file_path(self):
        """Edit tool shows file path."""
        detail = _extract_tool_detail("Edit", {"file_path": "/src/config.py"})
        assert "config.py" in detail

    def test_write_tool_shows_file_path(self):
        """Write tool shows file path."""
        detail = _extract_tool_detail("Write", {"file_path": "/new_file.txt"})
        assert "new_file.txt" in detail

    def test_bash_tool_shows_command(self):
        """Bash tool shows command."""
        detail = _extract_tool_detail("Bash", {"command": "npm install"})
        assert detail == "npm install"

    def test_bash_tool_truncates_long_command(self):
        """Long bash commands are truncated."""
        long_cmd = "npm install --save-dev typescript eslint prettier jest @types/node"
        detail = _extract_tool_detail("Bash", {"command": long_cmd})
        assert "..." in detail
        assert len(detail) <= 48  # 45 + "..."

    def test_glob_tool_shows_pattern(self):
        """Glob tool shows pattern."""
        detail = _extract_tool_detail("Glob", {"pattern": "**/*.py"})
        assert "**/*.py" in detail

    def test_glob_tool_shows_pattern_and_path(self):
        """Glob tool shows pattern and path."""
        detail = _extract_tool_detail("Glob", {"pattern": "*.ts", "path": "/src"})
        assert "*.ts" in detail
        assert "src" in detail

    def test_grep_tool_shows_pattern(self):
        """Grep tool shows search pattern."""
        detail = _extract_tool_detail("Grep", {"pattern": "TODO"})
        assert "`TODO`" in detail

    def test_grep_tool_shows_pattern_and_path(self):
        """Grep tool shows pattern and search path."""
        detail = _extract_tool_detail("Grep", {"pattern": "error", "path": "/logs"})
        assert "`error`" in detail
        assert "logs" in detail

    def test_webfetch_shows_domain(self):
        """WebFetch shows domain from URL."""
        detail = _extract_tool_detail("WebFetch", {"url": "https://api.example.com/data"})
        assert "api.example.com" in detail

    def test_websearch_shows_query(self):
        """WebSearch shows search query."""
        detail = _extract_tool_detail("WebSearch", {"query": "python asyncio"})
        assert '"python asyncio"' in detail

    def test_task_shows_description(self):
        """Task tool shows description."""
        detail = _extract_tool_detail("Task", {"description": "Explore codebase"})
        assert '"Explore codebase"' in detail

    def test_todowrite_shows_count(self):
        """TodoWrite shows item count."""
        detail = _extract_tool_detail("TodoWrite", {"todos": [1, 2, 3]})
        assert "3 items" in detail

    def test_todowrite_single_item(self):
        """TodoWrite shows singular for 1 item."""
        detail = _extract_tool_detail("TodoWrite", {"todos": [1]})
        assert "1 item" in detail

    def test_unknown_tool_returns_none(self):
        """Unknown tool returns None."""
        detail = _extract_tool_detail("UnknownTool", {"some": "data"})
        assert detail is None

    def test_empty_input_returns_none(self):
        """Empty input returns None."""
        detail = _extract_tool_detail("Read", None)
        assert detail is None
        detail = _extract_tool_detail("Read", {})
        assert detail is None


class TestFormatProgressMessage:
    """Test format_progress_message function."""

    def test_init_event(self):
        """Init event shows startup message."""
        event = ClaudeEvent(
            type=ClaudeEventType.INIT,
            data={"session_id": "abc123"},
            raw_line="{}",
        )
        msg = format_progress_message(event)
        assert "Starting" in msg or "Claude" in msg

    def test_user_event(self):
        """User event shows tool result processing."""
        event = ClaudeEvent(
            type=ClaudeEventType.USER,
            data={},
            raw_line="{}",
        )
        msg = format_progress_message(event)
        assert "tool result" in msg.lower() or "processing" in msg.lower()

    def test_result_success(self):
        """Result success event shows completion."""
        event = ClaudeEvent(
            type=ClaudeEventType.RESULT,
            data={
                "subtype": "success",
                "duration_ms": 1234,
                "total_cost_usd": 0.01,
            },
            raw_line="{}",
        )
        msg = format_progress_message(event)
        assert "Completed" in msg or "1234" in msg

    def test_result_failure(self):
        """Result failure event shows error."""
        event = ClaudeEvent(
            type=ClaudeEventType.RESULT,
            data={
                "subtype": "error",
                "result": "Something went wrong",
            },
            raw_line="{}",
        )
        msg = format_progress_message(event)
        assert "Failed" in msg or "error" in msg.lower()

    def test_assistant_with_text(self):
        """Assistant event with text shows preview."""
        message = Message(
            role="assistant",
            content=[ContentBlock(type="text", text="Let me analyze the code...")],
        )
        event = ClaudeEvent(
            type=ClaudeEventType.ASSISTANT,
            data={"message": {"role": "assistant", "content": [{"type": "text", "text": "Let me analyze..."}]}},
            raw_line="{}",
            message=message,
        )
        msg = format_progress_message(event)
        # Should contain thought emoji or text preview
        assert "analyze" in msg.lower() or "thinking" in msg.lower()

    def test_assistant_with_tool_use(self):
        """Assistant event with tool use shows tool info."""
        message = Message(
            role="assistant",
            content=[
                ContentBlock(
                    type="tool_use",
                    name="Read",
                    input={"file_path": "/src/main.py"},
                )
            ],
        )
        event = ClaudeEvent(
            type=ClaudeEventType.ASSISTANT,
            data={"message": {"role": "assistant", "content": []}},
            raw_line="{}",
            message=message,
        )
        msg = format_progress_message(event)
        assert "Read" in msg
        assert "main.py" in msg

    def test_assistant_with_multiple_tools(self):
        """Assistant event with multiple tools shows all."""
        message = Message(
            role="assistant",
            content=[
                ContentBlock(type="tool_use", name="Read", input={"file_path": "/a.py"}),
                ContentBlock(type="tool_use", name="Bash", input={"command": "ls"}),
            ],
        )
        event = ClaudeEvent(
            type=ClaudeEventType.ASSISTANT,
            data={"message": {}},
            raw_line="{}",
            message=message,
        )
        msg = format_progress_message(event)
        assert "Read" in msg
        assert "Bash" in msg
        assert "|" in msg  # Multiple tools separated by |

    def test_assistant_no_content_shows_thinking(self):
        """Assistant event with no content shows thinking message."""
        message = Message(role="assistant", content=[])
        event = ClaudeEvent(
            type=ClaudeEventType.ASSISTANT,
            data={"message": {}},
            raw_line="{}",
            message=message,
        )
        msg = format_progress_message(event)
        assert "thinking" in msg.lower()


class TestToolEmojis:
    """Test TOOL_EMOJIS constant."""

    def test_common_tools_have_emojis(self):
        """Common tools should have assigned emojis."""
        expected_tools = ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
        for tool in expected_tools:
            assert tool in TOOL_EMOJIS, f"Tool {tool} should have an emoji"

    def test_emojis_are_strings(self):
        """All emojis should be non-empty strings."""
        for tool, emoji in TOOL_EMOJIS.items():
            assert isinstance(emoji, str), f"Emoji for {tool} should be string"
            assert len(emoji) > 0, f"Emoji for {tool} should not be empty"
