"""Unified handler for choice, question, and confirmation interactions.

Permissions are handled natively via --permission-prompt-tool mechanism
and do NOT go through this handler.
"""

import json
import logging
from typing import Any

from ..models.events import ClaudeEvent, ClaudeEventType
from ..models.interactions import (
    ChoiceQuestion,
    Confirmation,
    TextQuestion,
)
from .stream_parser import extract_text_content

logger = logging.getLogger(__name__)

# JSON marker keys
MARKER_CHOICE = "__user_choice__"
MARKER_QUESTION = "__user_question__"
MARKER_CONFIRMATION = "__confirmation__"


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
        data = self._extract_json_marker(text, MARKER_CHOICE)
        if not data:
            return None

        try:
            return ChoiceQuestion(
                question=data["question"],
                options=data["options"],
                multiSelect=data.get("multiSelect", False),
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Malformed choice JSON data: {e}")
            return None

    def _detect_question(self, text: str) -> TextQuestion | None:
        """Detect and extract __user_question__ marker.

        Args:
            text: Claude's output text

        Returns:
            TextQuestion if found, None otherwise
        """
        data = self._extract_json_marker(text, MARKER_QUESTION)
        if not data:
            return None

        try:
            return TextQuestion(
                question=data["question"],
                default=data.get("default", ""),
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Malformed question JSON data: {e}")
            return None

    def _detect_confirmation(self, text: str) -> Confirmation | None:
        """Detect and extract __confirmation__ marker.

        Args:
            text: Claude's output text

        Returns:
            Confirmation if found, None otherwise
        """
        data = self._extract_json_marker(text, MARKER_CONFIRMATION)
        if not data:
            return None

        try:
            return Confirmation(
                question=data["question"],
                warning=data.get("warning"),
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Malformed confirmation JSON data: {e}")
            return None

    def _extract_json_marker(self, text: str, marker: str) -> dict[str, Any] | None:
        """Extract JSON marker data using proper JSON parsing with balanced braces.

        This method properly handles nested objects unlike regex-based approaches.

        Args:
            text: Text to search in
            marker: Marker key to look for (e.g., "__user_choice__")

        Returns:
            Extracted data dict or None if not found
        """
        # Find the marker in text
        marker_str = f'"{marker}"'
        idx = text.find(marker_str)
        if idx == -1:
            return None

        # Find the opening brace before the marker
        # Go backwards to find the start of the JSON object
        start_idx = text.rfind("{", 0, idx)
        if start_idx == -1:
            return None

        # Parse JSON starting from the opening brace using balanced braces
        try:
            # Try to find the complete JSON object using balanced braces
            json_str = self._extract_balanced_json(text, start_idx)
            if not json_str:
                return None

            full_obj = json.loads(json_str)
            if marker in full_obj and isinstance(full_obj[marker], dict):
                return dict(full_obj[marker])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON marker {marker}: {e}")
            logger.debug(f"Text around marker: {text[max(0, idx-50):idx+100]}")

        return None

    def _extract_balanced_json(self, text: str, start_idx: int) -> str | None:
        """Extract a complete JSON object using balanced brace counting.

        Args:
            text: Text to extract from
            start_idx: Index of opening brace

        Returns:
            Complete JSON string or None if not balanced
        """
        if start_idx >= len(text) or text[start_idx] != "{":
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i in range(start_idx, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == "\\":
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx:i + 1]

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
