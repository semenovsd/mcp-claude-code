"""Unified handler for choice, question, and confirmation interactions.

Permissions are handled natively via --permission-prompt-tool mechanism
and do NOT go through this handler.
"""

import json
import logging
import re
from typing import Any

from ..models.events import ClaudeEvent, ClaudeEventType
from ..models.interactions import (
    ChoiceQuestion,
    Confirmation,
    TextQuestion,
)
from .stream_parser import extract_text_content

logger = logging.getLogger(__name__)

# Regex patterns for detecting JSON markers in Claude's output
CHOICE_PATTERN = r'\{"\s*__user_choice__"\s*:\s*(\{[^}]+\})\s*\}'
QUESTION_PATTERN = r'\{"\s*__user_question__"\s*:\s*(\{[^}]+\})\s*\}'
CONFIRMATION_PATTERN = r'\{"\s*__confirmation__"\s*:\s*(\{[^}]+\})\s*\}'


class InteractionHandler:
    """Handler for choice, question, and confirmation interactions.

    Detects interaction markers in Claude output, calls appropriate
    MCP elicitation, and formats responses for Claude.

    Note:
        Permissions are handled natively via --permission-prompt-tool
        and do not go through this handler.

    Attributes:
        ctx: MCP context for elicitation
        last_question: Last text question asked (for context in responses)
        last_choice: Last choice question asked (for context in responses)
        last_confirmation: Last confirmation asked (for context in responses)
    """

    def __init__(self, ctx: Any) -> None:
        """Initialize handler.

        Args:
            ctx: MCP Context for calling elicit()
        """
        self.ctx = ctx
        # Track last interaction for providing context in responses
        self.last_question: TextQuestion | None = None
        self.last_choice: ChoiceQuestion | None = None
        self.last_confirmation: Confirmation | None = None

    async def handle_event(self, event: ClaudeEvent) -> dict[str, Any] | None:
        """Check event for interactions and handle them.

        Args:
            event: Claude event to process

        Returns:
            Dictionary with type and text response, or None if no interaction detected
            Example: {"type": "permission", "text": "PERMISSION_GRANTED: Allow Once"}
        """
        logger.info(f"[InteractionHandler] 🔍 handle_event called - event.type={event.type}")

        # Only process assistant events
        if event.type != ClaudeEventType.ASSISTANT:
            logger.debug(f"[InteractionHandler] Skipping non-ASSISTANT event: {event.type}")
            return None

        # Extract text content
        text_content = extract_text_content(event)
        logger.info(f"[InteractionHandler] Extracted text_content length: {len(text_content) if text_content else 0}")
        if text_content:
            logger.info(f"[InteractionHandler] Text content: {text_content[:500]}")

        if not text_content:
            logger.debug("[InteractionHandler] No text content found")
            return None

        # Try each interaction type (permissions handled natively via --permission-prompt-tool)
        if choice := self._detect_choice(text_content):
            logger.info(f"[InteractionHandler] 🔘 CHOICE DETECTED: {choice.question}")
            return await self._handle_choice(choice)

        if question := self._detect_question(text_content):
            logger.info(f"[InteractionHandler] ❓ QUESTION DETECTED: {question.question}")
            logger.info(f"[InteractionHandler] 📞 Calling _handle_question (will trigger elicitation)...")
            result = await self._handle_question(question)
            logger.info(f"[InteractionHandler] ✅ _handle_question returned: {result}")
            return result

        if confirm := self._detect_confirmation(text_content):
            logger.info(f"[InteractionHandler] ✓ CONFIRMATION DETECTED: {confirm.question}")
            return await self._handle_confirmation(confirm)

        logger.debug("[InteractionHandler] No interaction markers found")
        return None

    def _detect_choice(self, text: str) -> ChoiceQuestion | None:
        """Detect and extract __user_choice__ marker.

        Args:
            text: Claude's output text

        Returns:
            ChoiceQuestion if found, None otherwise
        """
        match = re.search(CHOICE_PATTERN, text)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            return ChoiceQuestion(
                question=data["question"],
                options=data["options"],
                multiSelect=data.get("multiSelect", False),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Malformed choice JSON: {e}")
            return None

    def _detect_question(self, text: str) -> TextQuestion | None:
        """Detect and extract __user_question__ marker.

        Args:
            text: Claude's output text

        Returns:
            TextQuestion if found, None otherwise
        """
        match = re.search(QUESTION_PATTERN, text)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            return TextQuestion(
                question=data["question"],
                default=data.get("default", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Malformed question JSON: {e}")
            return None

    def _detect_confirmation(self, text: str) -> Confirmation | None:
        """Detect and extract __confirmation__ marker.

        Args:
            text: Claude's output text

        Returns:
            Confirmation if found, None otherwise
        """
        match = re.search(CONFIRMATION_PATTERN, text)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            return Confirmation(
                question=data["question"],
                warning=data.get("warning"),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Malformed confirmation JSON: {e}")
            return None

    async def _handle_choice(self, choice: ChoiceQuestion) -> dict[str, Any]:
        """Handle choice question.

        Args:
            choice: Choice question data

        Returns:
            Response dict with type, text, and question context
        """
        # Store for context in resume
        self.last_choice = choice

        result = await self.ctx.elicit(choice.question, response_type=choice.options)

        if result.action != "accept":
            # User declined - pick first option as default
            selected = choice.options[0]
        else:
            selected = result.data

        return {
            "type": "choice",
            "text": f"I choose: {selected}",
            "question_text": choice.question,  # Original question for context
        }

    async def _handle_question(self, question: TextQuestion) -> dict[str, Any]:
        """Handle text question.

        Args:
            question: Text question data

        Returns:
            Response dict with type, text, and question context
        """
        # Store for context in resume
        self.last_question = question

        logger.debug(f"[InteractionHandler] 🔍 Calling ctx.elicit() for question: {question.question}")
        result = await self.ctx.elicit(question.question, response_type=str)
        logger.debug(f"[InteractionHandler] 🔍 Elicit result: action={result.action}, data={result.data}, type={type(result)}")

        if result.action != "accept":
            answer = question.default or "Skipped"
        else:
            answer = result.data

        logger.debug(f"[InteractionHandler] 🔍 Final answer: {answer}")
        # Return with question context for better resume handling
        return {
            "type": "question",
            "text": answer,
            "question_text": question.question,  # Original question for context
        }

    async def _handle_confirmation(self, confirm: Confirmation) -> dict[str, Any]:
        """Handle confirmation.

        Args:
            confirm: Confirmation data

        Returns:
            Response dict with type, text, and question context
        """
        # Store for context in resume
        self.last_confirmation = confirm

        message = confirm.question
        if confirm.warning:
            message += f"\n\nWARNING: {confirm.warning}"

        result = await self.ctx.elicit(message, response_type=bool)

        if result.action != "accept" or not result.data:
            confirmed = "No"
        else:
            confirmed = "Yes"

        return {
            "type": "confirmation",
            "text": f"CONFIRMED: {confirmed}",
            "question_text": confirm.question,  # Original question for context
        }
