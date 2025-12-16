# Claude Code Development Guide

Project-specific instructions for AI assistants working on this codebase.

---

## MCP Tool Reference

### execute_claude

```python
@mcp.tool(
    annotations={
        "title": "Claude Code",
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": True,
    }
)
async def execute_claude(
    prompt: Annotated[str, Field(description="Task for Claude Code")],
    model: Annotated[str, Field(
        default="haiku",
        description="haiku=fast/simple, sonnet=complex, opus=critical"
    )] = "haiku",
    workspace_root: Annotated[str | None, Field(...)] = None,
    skip_permissions: Annotated[bool, Field(...)] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delegate coding tasks to Claude Code CLI.
    Interactive: permissions, questions, confirmations.
    Model: haiku=fast/simple, sonnet=complex, opus=critical.
    """
```

**Model Selection:**
- `haiku` (default): Fast, cost-effective for simple tasks (file ops, basic edits)
- `sonnet`: Complex reasoning tasks (refactoring, architecture)
- `opus`: Critical decisions requiring maximum capability

**Behavior:**
- `skip_permissions=False` (default): Interactive mode with MCP Elicitation
- `skip_permissions=True`: Uses `--dangerously-skip-permissions`

---

## Configuration (Settings)

All settings can be overridden via environment variables with `MCP_CLAUDE_` prefix:

```python
class Settings(BaseSettings):
    # Claude Code CLI
    claude_code_path: str = "claude"
    default_model: str = "sonnet"

    # Timeouts
    max_execution_seconds: int = 600
    inactivity_timeout_seconds: int = 120

    # Permission-specific timeouts (user may be away)
    permission_timeout_seconds: int = 3600  # 60 minutes
    socket_read_timeout_seconds: int = 120

    # Retry settings for Unix socket
    socket_retry_attempts: int = 3
    socket_retry_delay_seconds: float = 0.1  # Exponential backoff

    # Storage
    permission_storage_path: str = "~/.mcp-claude-code/permissions.json"
```

**Environment variables examples:**
```bash
MCP_CLAUDE_PERMISSION_TIMEOUT_SECONDS=1800  # 30 minutes
MCP_CLAUDE_SOCKET_RETRY_ATTEMPTS=5
```

---

## Progress Indication System

### Key Components

**Files:**
- `src/mcp_claude_code/models/events.py` - `ClaudeEvent`, `ContentBlock`, `Message` dataclasses
- `src/mcp_claude_code/executor/stream_parser.py` - `format_progress_message()`, `_extract_tool_detail()`
- `src/mcp_claude_code/executor/interactive_executor.py` - `_report_progress()`, heartbeat mechanism

### Progress Message Format

```python
TOOL_EMOJIS = {
    "Read": "📖", "Edit": "✏️", "Write": "📝",
    "Bash": "💻", "Glob": "🔍", "Grep": "🔎",
    "WebFetch": "🌐", "WebSearch": "🔍", "Task": "📋",
    "TodoWrite": "📝", "NotebookEdit": "📓",
}
```

Examples:
- `"📖 Read: src/main.py"`
- `"💻 Bash: npm install"`
- `"🔎 Grep: 'TODO' in src/"`

### Heartbeat Mechanism

Sends `"⏳ Still working... (Xs elapsed)"` every 5 seconds.

> **Note:** Progress tokens do NOT extend timeouts in Cursor 2.2.1+ (known Cursor bug).

---

## Native Permission System

### Architecture

```
Claude Code CLI
    │ When tool needs permission
    ↓
--permission-prompt-tool mcp__perm__approve
    │
    ↓
Approver MCP Server (approver.py)
    │ Unix socket IPC
    ↓
CallbackServer (callback_server.py)
    │ ctx.elicit()
    ↓
Cursor/Cline UI: [Allow Once] [Allow Session] [Allow Always] [Deny]
```

### Key Files

- `src/mcp_claude_code/permission_server/approver.py` - MCP permission tool with retry logic
- `src/mcp_claude_code/permission_server/callback_server.py` - Unix socket elicitation bridge
- `src/mcp_claude_code/storage/permission_manager.py` - Permission caching and persistence

### Permission Response Enum

```python
class PermissionResponse(Enum):
    """User-facing permission response labels for MCP Elicitation UI."""
    ALLOW_ONCE = "Allow Once"
    ALLOW_SESSION = "Allow Session"
    ALLOW_ALWAYS = "Allow Always"
    DENY = "Deny"

    @classmethod
    def all_options(cls) -> list[str]: ...
    @classmethod
    def from_string(cls, value: str) -> "PermissionResponse": ...
    def to_decision(self) -> PermissionDecision: ...
```

### Retry Logic (approver.py)

The approver uses exponential backoff for Unix socket connections:

```python
async def request_permission_via_socket(
    socket_path: str,
    tool_name: str,
    tool_input: dict,
    timeout_seconds: float = 3600,    # 60 min - user may be away
    retry_attempts: int = 3,
    retry_delay: float = 0.1,         # Exponential backoff
) -> dict: ...
```

---

## Interactive Protocols

### JSON Markers

| Type | Marker | Example |
|------|--------|---------|
| Choice | `__user_choice__` | `{"__user_choice__": {"question": "Q?", "options": ["A", "B"]}}` |
| Question | `__user_question__` | `{"__user_question__": {"question": "Name?", "default": ""}}` |
| Confirmation | `__confirmation__` | `{"__confirmation__": {"question": "Proceed?", "warning": "..."}}` |

### Handler

**File:** `src/mcp_claude_code/executor/interaction_handler.py`

Detects JSON markers in Claude output using balanced brace parsing (not regex) and calls `ctx.elicit()` to show UI in IDE.

```python
def _extract_json_marker(self, text: str, marker: str) -> dict[str, Any] | None:
    """Extract JSON marker data using proper JSON parsing with balanced braces.
    Properly handles nested objects unlike regex-based approaches."""

def _extract_balanced_json(self, text: str, start_idx: int) -> str | None:
    """Extract complete JSON object using balanced brace counting."""
```

---

## Graceful Shutdown

The server supports graceful shutdown on SIGTERM/SIGINT:

```python
async def _graceful_shutdown(sig: signal.Signals) -> None:
    """Handle graceful shutdown on signal."""
    # Terminates all active executors
    # Cleans up permission servers
    # Removes temporary files
```

---

## Testing

> **STRICT RULE: ONLY REAL TESTS**
>
> **FORBIDDEN:**
> - Mock tests (unittest.mock, patch, MagicMock, AsyncMock)
> - Fake objects, stub implementations
>
> **REQUIRED:**
> - Real Claude Code CLI
> - Real API calls
> - Real file operations
>
> **Test locations:**
> - E2E tests: `tests/test_e2e/`
> - Unit tests (pure functions): `tests/test_unit/`

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/mcp_claude_code/server.py` | FastMCP server with `execute_claude` tool |
| `src/mcp_claude_code/executor/interactive_executor.py` | Spawns CLI, handles events, manages stdin |
| `src/mcp_claude_code/executor/interaction_handler.py` | Detects and handles JSON markers |
| `src/mcp_claude_code/executor/stream_parser.py` | Parses NDJSON stream, formats progress |
| `src/mcp_claude_code/prompts.py` | Protocol definitions, `get_system_prompt()` |
| `src/mcp_claude_code/permission_server/approver.py` | MCP permission tool |
| `src/mcp_claude_code/permission_server/callback_server.py` | Unix socket elicitation bridge |
| `src/mcp_claude_code/storage/permission_manager.py` | Permission caching and persistence |
| `src/mcp_claude_code/models/interactions.py` | `PermissionResponse`, `PermissionDecision` enums |
| `src/mcp_claude_code/config.py` | `Settings` with configurable timeouts |
