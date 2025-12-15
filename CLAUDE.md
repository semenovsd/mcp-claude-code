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

- `src/mcp_claude_code/permission_server/approver.py` - MCP permission tool
- `src/mcp_claude_code/permission_server/callback_server.py` - Unix socket elicitation bridge
- `src/mcp_claude_code/storage/permission_manager.py` - Permission caching and persistence

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

Detects JSON markers in Claude output and calls `ctx.elicit()` to show UI in IDE.

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
