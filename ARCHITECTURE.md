# Architecture

Detailed architecture documentation for mcp-claude-code MCP server.

## System Overview

mcp-claude-code is an MCP (Model Context Protocol) server that proxies requests to Claude Code CLI with full interactive capabilities.

### Key Principles

- **Native Permissions** - Handled via `--permission-prompt-tool`
- **System Prompt via CLI** - Protocols passed via `--append-system-prompt`
- **Interactive Mode** - Full support for questions, choices, confirmations
- **Real Tests Only** - All tests use real Claude Code CLI

### Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  MCP Client (Cursor/Cline)                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  MCP Elicitation UI                                                  │    │
│  │  • Permissions: Allow Once / Allow Session / Allow Always / Deny    │    │
│  │  • Choice Questions, Text Questions, Confirmations                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ MCP Protocol (stdio/sse)
                                ↓
┌───────────────────────────────────────────────────────────────────────────────┐
│  mcp-claude-code MCP Server (FastMCP)                                        │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌────────────────────────┐  │
│  │ InteractiveExecutor│  │ CallbackServer      │  │ PermissionManager      │  │
│  │ - spawn CLI        │  │ - Unix socket       │  │ - JSON persistence     │  │
│  │ - stream events    │  │ - elicit() bridge   │  │ - session caching      │  │
│  │ - handle stdin     │  │                     │  │                        │  │
│  └─────────┬──────────┘  └──────────┬──────────┘  └──────────┬─────────────┘  │
└────────────│────────────────────────│───────────────────────│─────────────────┘
             │                        │                        │
             │ subprocess             │ Unix socket            │ JSON file
             ↓                        ↓                        ↓
┌───────────────────────┐  ┌────────────────────┐  ┌──────────────────────────┐
│ Claude Code CLI       │  │ Approver MCP       │  │ permissions.json         │
│ --output-format       │  │ (permission tool)  │  │                          │
│   stream-json         │  └────────────────────┘  └──────────────────────────┘
│ --append-system-prompt│
│ --permission-prompt-  │
│   tool mcp__perm__    │
│   approve             │
└───────────────────────┘
```

---

## Native Permission System

### Architecture

Permissions are handled via Claude Code CLI's native mechanism:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Claude Code CLI                                                             │
│  When using a tool (Read, Write, Bash, etc.)                                │
│  CLI calls permission-prompt-tool instead of showing terminal dialog        │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ MCP Tool Call: mcp__perm__approve
                                ↓
┌───────────────────────────────────────────────────────────────────────────────┐
│  Approver MCP Server (approver.py)                                            │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │  @mcp.tool() approve(tool_name, tool_input, risk_level)                │   │
│  │                                                                         │   │
│  │  1. Check cache (Allow Session/Always)                                 │   │
│  │  2. If not cached → send request via Unix socket                       │   │
│  │  3. Receive response from CallbackServer                               │   │
│  │  4. Store decision in cache if needed                                  │   │
│  │  5. Return {"decision": "allow"/"deny", "message": "..."}              │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │ Unix Socket IPC
                                ↓
┌───────────────────────────────────────────────────────────────────────────────┐
│  CallbackServer (callback_server.py)                                          │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │  Listens on /tmp/mcp_callback_{uuid}.sock                              │   │
│  │                                                                         │   │
│  │  1. Receive request from approver.py                                   │   │
│  │  2. Call ctx.elicit() with enum schema                                 │   │
│  │  3. Show UI in Cursor/Cline                                            │   │
│  │  4. Return user's decision                                             │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │ MCP Elicitation
                                ↓
┌───────────────────────────────────────────────────────────────────────────────┐
│  Cursor/Cline UI                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │  "Allow Write: /path/to/file.txt"                                      │   │
│  │  [Allow Once] [Allow Session] [Allow Always] [Deny]                    │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
```

### CLI Flags

```bash
claude \
  --output-format stream-json \
  --input-format stream-json \
  --strict-mcp-config \
  --mcp-config /tmp/approver_config_{uuid}.json \
  --permission-prompt-tool mcp__perm__approve
```

### Permission Decisions

| Decision | Behavior |
|----------|----------|
| Allow Once | Allow only this request |
| Allow Session | Cache until session ends |
| Allow Always | Store in permissions.json |
| Deny | Reject operation |

---

## System Prompt Architecture

### Using --append-system-prompt

Interaction protocols are passed via `--append-system-prompt`:

```python
def get_system_prompt(
    enable_choices: bool = False,
    enable_questions: bool = False,
    enable_confirmations: bool = False,
) -> str | None:
    """Get system prompt for --append-system-prompt flag."""
    protocols = []
    if enable_choices:
        protocols.append(CHOICE_QUESTION_PROTOCOL)
    if enable_questions:
        protocols.append(TEXT_QUESTION_PROTOCOL)
    if enable_confirmations:
        protocols.append(CONFIRMATION_PROTOCOL)
    return "\n\n".join(protocols) if protocols else None
```

### Advantages

- **Clean user prompt** - User prompt is not modified
- **Proper separation** - System instructions separated from task
- **Native support** - Uses built-in Claude Code mechanism

### No Duplication

System prompt is added only on first execution:
- First `execute()` → passes `--append-system-prompt`
- `_resume_session()` → does NOT pass system_prompt
- Claude Code preserves context via `--resume SESSION_ID`

---

## Interactive Protocols

### Choice Questions

JSON marker: `{"__user_choice__": {...}}`

```json
{"__user_choice__": {
  "question": "Which package manager?",
  "options": ["pip", "poetry", "conda"],
  "multiSelect": false
}}
```

### Text Questions

JSON marker: `{"__user_question__": {...}}`

```json
{"__user_question__": {
  "question": "What is the project name?",
  "default": "myproject"
}}
```

### Confirmations

JSON marker: `{"__confirmation__": {...}}`

```json
{"__confirmation__": {
  "question": "Delete all files in /tmp?",
  "warning": "This action cannot be undone"
}}
```

### Handling via stdin

```python
# Response sent back to Claude via stdin
await self._send_stdin_message(answer_text)

# stdin message format:
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [{"type": "text", "text": "I choose: poetry"}]
  }
}
```

---

## Progress Indication System

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Claude Code CLI (stream-json)                                               │
│  NDJSON events: init, assistant, user, result                                │
└──────────────────────────────────│──────────────────────────────────────────┘
                                   │ stdout stream
                                   ↓
┌───────────────────────────────────────────────────────────────────────────────┐
│  format_progress_message(event)                                               │
│  TOOL_EMOJIS = {"Read": "📖", "Bash": "💻", "Grep": "🔎", ...}                │
│                                                                                │
│  Output examples:                                                              │
│  • "📖 Read: src/main.py"                                                     │
│  • "💻 Bash: npm install"                                                     │
│  • "🔎 Grep: `TODO` in src/"                                                  │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Heartbeat Mechanism

```python
async def heartbeat_task():
    while heartbeat_running:
        await asyncio.sleep(5)
        await ctx.report_progress(
            progress=heartbeat_count,
            total=None,
            message=f"⏳ Still working... ({elapsed}s elapsed)",
        )
```

### Cursor Timeout Limitation

> **Note:** Progress tokens do NOT extend timeout in Cursor 2.2.1+. This is a known Cursor bug.

---

## Documentation Links

### MCP Protocol
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [MCP Elicitation](https://spec.modelcontextprotocol.io/specification/server/elicitation/)

### FastMCP (Python SDK)
- [FastMCP Documentation](https://gofastmcp.com/)
- [FastMCP Elicitation](https://gofastmcp.com/servers/elicitation)

### Claude Code CLI
- [Claude Code Documentation](https://docs.claude.com/claude-code)
- [CLI Reference](https://docs.claude.com/claude-code/cli-reference)

### Cursor IDE
- [Cursor MCP Support](https://docs.cursor.com/context/model-context-protocol)
