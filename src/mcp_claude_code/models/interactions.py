"""Interaction models for Claude Code interactive communication.

Permissions are handled natively via --permission-prompt-tool.
This module provides models for:
- Permission decisions and storage
- Choice questions
- Text questions
- Confirmations
"""

from dataclasses import dataclass
from enum import Enum


class PermissionDecision(Enum):
    """Permission decision types for native permission handling."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


@dataclass
class ChoiceQuestion:
    """Choice question from Claude.

    This is extracted from JSON marker: {"__user_choice__": {...}}

    Attributes:
        question: The question to ask
        options: List of options to choose from
        multiSelect: Whether multiple selections are allowed
    """

    question: str
    options: list[str]
    multiSelect: bool = False  # noqa: N815


@dataclass
class TextQuestion:
    """Free-form text question from Claude.

    This is extracted from JSON marker: {"__user_question__": {...}}

    Attributes:
        question: The question to ask
        default: Default value
    """

    question: str
    default: str = ""


@dataclass
class Confirmation:
    """Yes/No confirmation from Claude.

    This is extracted from JSON marker: {"__confirmation__": {...}}

    Attributes:
        question: The confirmation question
        warning: Optional warning message
    """

    question: str
    warning: str | None = None


@dataclass
class StoredPermission:
    """Stored permission entry.

    Attributes:
        action: Tool name
        target: File path or command
        decision: Permission decision type
        hash: Unique hash for deduplication
    """

    action: str
    target: str
    decision: PermissionDecision
    hash: str
