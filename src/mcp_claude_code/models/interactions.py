"""Interaction models for Claude Code interactive communication.

Permissions are handled natively via --permission-prompt-tool.
This module provides models for:
- Permission decisions and storage
- Permission responses (UI labels)
- Choice questions
- Text questions
- Confirmations
"""

from dataclasses import dataclass
from enum import Enum


class PermissionDecision(Enum):
    """Permission decision types for native permission handling (internal storage)."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class PermissionResponse(Enum):
    """User-facing permission response labels for MCP Elicitation UI.

    These are the exact strings shown to users in permission dialogs.
    """

    ALLOW_ONCE = "Allow Once"
    ALLOW_SESSION = "Allow Session"
    ALLOW_ALWAYS = "Allow Always"
    DENY = "Deny"

    @classmethod
    def all_options(cls) -> list[str]:
        """Get all permission options as a list of strings for elicitation."""
        return [opt.value for opt in cls]

    @classmethod
    def from_string(cls, value: str) -> "PermissionResponse":
        """Convert string to PermissionResponse enum.

        Args:
            value: String value like "Allow Once"

        Returns:
            Corresponding PermissionResponse enum

        Raises:
            ValueError: If value doesn't match any option
        """
        for opt in cls:
            if opt.value == value:
                return opt
        raise ValueError(f"Invalid permission response: {value}")

    def to_decision(self) -> PermissionDecision:
        """Convert UI response to internal decision type."""
        mapping = {
            PermissionResponse.ALLOW_ONCE: PermissionDecision.ALLOW_ONCE,
            PermissionResponse.ALLOW_SESSION: PermissionDecision.ALLOW_SESSION,
            PermissionResponse.ALLOW_ALWAYS: PermissionDecision.ALLOW_ALWAYS,
            PermissionResponse.DENY: PermissionDecision.DENY,
        }
        return mapping[self]


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
