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

### Why Custom Permission Caching is Required

**Critical insight:** Claude Code CLI's `--permission-prompt-tool` does NOT support scoped permissions!

The response format is strictly:
```json
// Allow (one-time only):
{"behavior": "allow", "updatedInput": {...}}

// Deny:
{"behavior": "deny", "message": "..."}
```

**There is NO field for:**
- `destination: "session"` / `"localSettings"` / `"userSettings"`
- `scope: "always"` / `"session"`
- Any "remember this decision" mechanism

**Result:** Without our custom cache, users would see a permission dialog for EVERY tool call!

### Claude Code CLI Native Permissions (Interactive TUI)

When running Claude Code interactively (without `--permission-prompt-tool`), users see:
```
Do you want to allow Claude to fetch this content?
❯ 1. Yes
  2. Yes, and don't ask again for github.com
  3. No, and tell Claude what to do differently (esc)
```

Option 2 saves to Claude Code's internal settings with destinations:
| Destination | File | Description |
|-------------|------|-------------|
| `session` | In-memory | Until CLI closes |
| `localSettings` | `.claude/settings.local.json` | Project (git-ignored) |
| `projectSettings` | `.claude/settings.json` | Project (version controlled) |
| `userSettings` | `~/.claude/settings.json` | Global |

**BUT:** This is only available in interactive TUI mode, NOT via `--permission-prompt-tool`!

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Cursor/Cline IDE                                    │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  UI: [Allow Once] [Allow Session] [Allow Always] [Deny]                   │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                             │
│                                    │ ctx.elicit()                                │
└────────────────────────────────────│────────────────────────────────────────────┘
                                     │
┌────────────────────────────────────│────────────────────────────────────────────┐
│                    MCP Claude Code Server (server.py)                            │
│                                    │                                             │
│  ┌─────────────────────────────────▼───────────────────────────────────────┐    │
│  │           InteractiveExecutor (interactive_executor.py)                  │    │
│  │                                                                          │    │
│  │  1. _setup_permission_server()                                          │    │
│  │     - Creates ElicitationCallbackServer (Unix socket)                   │    │
│  │     - Generates MCP config JSON                                         │    │
│  │     - Defines elicitation_callback()                                    │    │
│  │                                                                          │    │
│  │  2. _build_command() adds flags:                                        │    │
│  │     --strict-mcp-config                                                 │    │
│  │     --mcp-config /tmp/mcp-config-XXX.json                               │    │
│  │     --permission-prompt-tool mcp__perm__approve                         │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                    │                                             │
│                                    │ Unix Socket IPC                             │
│                                    │ /tmp/mcp-perm-XXX.sock                      │
│                                    │                                             │
│  ┌─────────────────────────────────▼───────────────────────────────────────┐    │
│  │        ElicitationCallbackServer (callback_server.py)                    │    │
│  │                                                                          │    │
│  │  - Receives: {"type": "permission_request", "tool_name": ...,           │    │
│  │               "tool_input": {...}}                                      │    │
│  │  - Calls elicitation_callback(tool_name, tool_input)                    │    │
│  │  - Returns: {"granted": true/false, "decision": "..."}                  │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │              PermissionManager (permission_manager.py)                    │    │
│  │                                                                          │    │
│  │  Session Storage: dict[hash] -> StoredPermission (in-memory)            │    │
│  │  Persistent Storage: JSON → ~/.mcp-claude-code/permissions.json         │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     │ subprocess (stdio MCP)
                                     │
┌────────────────────────────────────▼────────────────────────────────────────────┐
│                        Claude Code CLI                                           │
│                                                                                  │
│  Started with: --permission-prompt-tool mcp__perm__approve                      │
│  When permission needed → calls mcp__perm__approve                              │
└────────────────────────────────────▼────────────────────────────────────────────┘
                                     │
                                     │ stdio (JSON-RPC)
                                     │
┌────────────────────────────────────▼────────────────────────────────────────────┐
│                     Approver MCP Server (approver.py)                            │
│                                                                                  │
│  Tool: approve(tool_name, tool_input)                                           │
│                                                                                  │
│  1. Receives request from Claude Code CLI                                       │
│  2. Sends via Unix socket to ElicitationCallbackServer                          │
│  3. Waits for response (up to 60 min by default)                                │
│  4. Returns {"behavior": "allow"} or {"behavior": "deny"}                       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Permission Flow Sequence

1. **Claude Code CLI** wants to execute `Read("src/main.py")`
2. **Claude Code CLI** calls MCP tool `mcp__perm__approve`
3. **Approver Server** receives call, sends via Unix socket
4. **ElicitationCallbackServer** receives request
5. **elicitation_callback()** checks cache:
   - If found → return `{"granted": true}` immediately (no UI!)
   - If not found → call `ctx.elicit()` → show UI in IDE
6. **User** selects "Allow Session" / "Allow Always" / etc.
7. **elicitation_callback()** saves to cache if needed
8. Response flows back through the chain
9. **Claude Code CLI** receives `{"behavior": "allow"}` and executes tool

### Permission Caching Layers

| User Choice | Session Cache | Persistent JSON | Survives Restart? |
|-------------|---------------|-----------------|-------------------|
| Allow Once | ❌ | ❌ | ❌ |
| Allow Session | ✅ | ❌ | ❌ |
| Allow Always | ✅ | ✅ | ✅ |
| Deny | ❌ | ❌ | ❌ |

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

## Claude Code Hooks (Alternative Approach)

### Overview

Claude Code supports hooks configured in `settings.json`. Hooks run as **separate subprocesses** for each invocation.

### Hook Configuration Files

| File | Scope |
|------|-------|
| `~/.claude/settings.json` | Global (all projects) |
| `.claude/settings.json` | Project (version controlled) |
| `.claude/settings.local.json` | Local project (git-ignored) |

### Relevant Hook Types

| Hook | When | Can Block? |
|------|------|------------|
| `PreToolUse` | Before tool execution | ✅ allow/deny/ask |
| `PermissionRequest` | When permission dialog would show | ✅ allow/deny |
| `PostToolUse` | After tool execution | ⚠️ Feedback only |

### Hook Response Format

**PreToolUse:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",  // or "deny" or "ask"
    "permissionDecisionReason": "Auto-approved by policy"
  }
}
```

**PermissionRequest:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow"  // or "deny"
    }
  }
}
```

### Why Hooks Don't Replace Our MCP Approach

| Aspect | Hooks | MCP Permission Server |
|--------|-------|----------------------|
| Execution | Subprocess per call | Long-lived process |
| State | ❌ None (new process) | ✅ In-memory cache |
| UI Access | ❌ No ctx.elicit() | ✅ Full MCP elicitation |
| Scoped Permissions | ❌ No scope field | ✅ Custom implementation |

**Critical limitation:** Hooks cannot show custom UI and get user response. They can only:
- `allow` - permit without UI
- `deny` - block without UI
- `ask` - show Claude Code's standard UI (but cannot learn user's choice!)

### References

- [Hooks Reference](https://code.claude.com/docs/en/hooks)
- [SDK Permissions](https://code.claude.com/docs/en/sdk/sdk-permissions)
- [GitHub Issue #11073](https://github.com/anthropics/claude-code/issues/11073) - Scoped permissions request
- [GitHub Issue #1175](https://github.com/anthropics/claude-code/issues/1175) - Documentation request

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
