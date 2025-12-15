"""Prompt augmentation templates for Claude Code interaction protocols.

This module provides system prompt templates for Claude Code CLI interactive features.
The prompts are passed via --append-system-prompt flag to preserve default Claude Code behavior
while adding our interaction protocols.

Permissions are handled natively via --permission-prompt-tool mechanism.
This module only handles: choice questions, text questions, and confirmations.
"""

CHOICE_QUESTION_PROTOCOL = """
═══════════════════════════════════════════════════════════════
CRITICAL PROTOCOL - CHOICE QUESTIONS (MANDATORY)
═══════════════════════════════════════════════════════════════

When the task requires user selection from options, you MUST:

1. Output this EXACT JSON format as plain text:
{"__user_choice__": {"question": "Your question?", "options": ["option1", "option2", "option3"], "multiSelect": false}}

2. WAIT for response: "I choose: [option]"

3. Continue with selected option

EXAMPLE:
Task: "Ask which package manager, create config"
You output: {"__user_choice__": {"question": "Which package manager?", "options": ["pip", "poetry", "conda"], "multiSelect": false}}
I respond: I choose: poetry
You create: pyproject.toml file

REQUIRED: multiSelect=false for single choice, true for multiple
"""

TEXT_QUESTION_PROTOCOL = """
═══════════════════════════════════════════════════════════════
CRITICAL PROTOCOL - TEXT QUESTIONS (MANDATORY)
═══════════════════════════════════════════════════════════════

When the task requires user text input, you MUST:

1. Output this EXACT JSON format as plain text:
{"__user_question__": {"question": "Your question?", "default": ""}}

2. WAIT for response with user's text

3. Continue using that text IMMEDIATELY - do NOT ask again

CRITICAL RULES:
- After outputting __user_question__, ANY text you receive is the user's answer
- NEVER re-ask the same question - the response IS the answer
- Response format may be: "В ответ на вопрос 'X': Y" - use Y as the answer
- Even if response looks like a command (e.g., "stat"), it IS the answer to your question
- If user's answer seems unexpected, USE IT ANYWAY and proceed

EXAMPLE:
Task: "Ask user's name, create {name}.md"
You output: {"__user_question__": {"question": "What is your name?", "default": ""}}
I respond: John Smith
You create: "John Smith.md" file (do NOT ask again!)

REQUIRED: Always ask when information is needed, never guess. NEVER repeat questions.
"""

CONFIRMATION_PROTOCOL = """
═══════════════════════════════════════════════════════════════
CRITICAL PROTOCOL - CONFIRMATIONS (MANDATORY)
═══════════════════════════════════════════════════════════════

For destructive/risky operations, you MUST confirm:

1. Output this EXACT JSON format as plain text:
{"__confirmation__": {"question": "Action to confirm?", "warning": "Why risky (optional)"}}

2. WAIT for response: "CONFIRMED: Yes" or "CONFIRMED: No"

3. Proceed only if Yes

EXAMPLE:
Task: "Delete all .log files"
You output: {"__confirmation__": {"question": "Delete 15 .log files?", "warning": "Cannot be undone"}}
I respond: CONFIRMED: Yes
You execute: deletion command

REQUIRED: Use for any destructive operation (delete, overwrite, etc)
"""


def get_system_prompt(
    enable_choices: bool = False,
    enable_questions: bool = False,
    enable_confirmations: bool = False,
) -> str | None:
    """Get system prompt for Claude Code interactions.

    Returns combined protocol instructions for --append-system-prompt flag.
    These protocols instruct Claude to output JSON markers when user input
    is needed (choices, questions, confirmations).

    Permissions are handled natively via --permission-prompt-tool and
    do NOT require system prompt instructions.

    Args:
        enable_choices: Enable choice question protocol
        enable_questions: Enable text question protocol
        enable_confirmations: Enable confirmation protocol

    Returns:
        Combined system prompt string, or None if no protocols enabled

    Examples:
        >>> prompt = get_system_prompt(enable_choices=True)
        >>> "CHOICE QUESTIONS" in prompt
        True
        >>> get_system_prompt() is None
        True
    """
    protocols = []

    if enable_choices:
        protocols.append(CHOICE_QUESTION_PROTOCOL)
    if enable_questions:
        protocols.append(TEXT_QUESTION_PROTOCOL)
    if enable_confirmations:
        protocols.append(CONFIRMATION_PROTOCOL)

    if not protocols:
        return None

    return "\n\n".join(protocols)
