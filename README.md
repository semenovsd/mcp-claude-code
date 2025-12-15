# MCP Claude Code

**MCP Server for Claude Code CLI with full interactive capabilities.**

Provides seamless access to Claude Code CLI through MCP protocol with native IDE integration:

- **Permission Requests** - File/command approvals via MCP Elicitation
- **Text Questions** - Free-form user input
- **Choice Questions** - Dropdown selections
- **Confirmations** - Yes/no dialogs
- **Multi-Turn Conversations** - Session resumption support
- **Progress Indication** - Real-time tool activity

## How It Works

1. **JSON Protocol** - Claude outputs interaction markers (`__user_question__`, `__user_choice__`, `__confirmation__`)
2. **MCP Elicitation** - Markers are parsed and shown as native IDE dialogs
3. **Session Resumption** - Multi-turn via `--resume SESSION_ID`
4. **Native Permissions** - Uses `--permission-prompt-tool` mechanism

## Quick Start

### Prerequisites

- Python 3.13+
- Poetry
- Claude Code CLI ([download](https://claude.com/claude-code))
- Cursor 2.1.50+ (for MCP Elicitation support)

### Installation

```bash
git clone https://github.com/your-username/mcp-claude-code.git
cd mcp-claude-code
poetry install
```

### Cursor Configuration

Add to Cursor settings (`Cmd/Ctrl + Shift + P` → "Preferences: Open User Settings (JSON)"):

```json
{
  "mcpServers": {
    "claude-code": {
      "command": "poetry",
      "args": ["run", "python", "-m", "mcp_claude_code"],
      "cwd": "/absolute/path/to/mcp-claude-code",
      "env": {
        "WORKSPACE_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

## MCP Tool

### execute_claude

Execute Claude Code CLI with full interactive capabilities.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | required | Task for Claude Code |
| `model` | string | `"haiku"` | haiku=fast/simple, sonnet=complex, opus=critical |
| `workspace_root` | string | `$WORKSPACE_ROOT` | Working directory |
| `skip_permissions` | bool | `false` | Skip permission checks |

**Example:**

```json
{
  "prompt": "Create a README.md for this project",
  "model": "haiku",
  "skip_permissions": false
}
```

**Returns:**

```json
{
  "success": true,
  "output": "Created README.md with project documentation.",
  "error": null,
  "execution_time_seconds": 5.2,
  "permissions_requested": 1,
  "permissions_granted": 1,
  "choices_asked": 0,
  "questions_asked": 0,
  "confirmations_asked": 0
}
```

### Permission Modes

| `skip_permissions` | Behavior |
|-------------------|----------|
| `false` (default) | Interactive - permission requests shown via MCP Elicitation |
| `true` | Autonomous - uses `--dangerously-skip-permissions` |

### Permission Scopes

When approving a permission:

| Scope | Description | Storage |
|-------|-------------|---------|
| Allow Once | Only current operation | Not stored |
| Allow Session | Current session | Memory |
| Allow Always | Future sessions | JSON file |
| Deny | Reject operation | Not stored |

## Progress Indication

Real-time progress via MCP Progress Notifications:

| Event | Example |
|-------|---------|
| Read | `📖 Read: src/main.py` |
| Bash | `💻 Bash: npm install` |
| Grep | `🔎 Grep: 'TODO' in src/` |
| Heartbeat | `⏳ Still working... (15s elapsed)` |
| Success | `✅ Completed in 1234ms` |

## Testing

All tests use **real Claude Code CLI** - no mocks.

```bash
# Run all tests
poetry run pytest tests/ -v

# Run E2E tests
poetry run pytest tests/test_e2e/ -v
```

## Architecture

```
MCP Client (Cursor/Cline)
    │ MCP Protocol
    ↓
claude-code-proxy MCP Server
    │ subprocess
    ↓
Claude Code CLI
    --output-format stream-json
    --append-system-prompt (interaction protocols)
    --permission-prompt-tool mcp__perm__approve
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Client Support

| Client | Elicitation Support |
|--------|---------------------|
| Cursor 2.0+ | ✅ Full |
| VS Code + Cline | ⚠️ Depends on version |
| Claude Desktop | ❌ Not supported |

## License

MIT
