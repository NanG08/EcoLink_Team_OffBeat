"""
EcoLink agent core.

Wraps the Strands Agent with:
  - System prompt injection
  - Conversation state (DynamoDB) loaded before each turn
  - Guardrail checks (confirmation, hazardous items) that run in Python
    BEFORE the LLM/tool pipeline so they cannot be bypassed by prompting
  - Structured logging of every agent invocation
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from strands import Agent

from agent.api_client import EcoLinkAPIClient
from agent.prompt import SYSTEM_PROMPT
from agent.state import ConversationState
import agent.tools as tools_module
from agent.tools import (
    classify_image,
    create_pickup,
    ewaste_quote,
    find_recyclers,
    get_price,
    offer_to_recycler,
    update_pickup,
    HAZARDOUS_LABELS,
)

logger = logging.getLogger(__name__)

# Words that count as an explicit "yes" from the user (case-insensitive).
_YES_PATTERN = re.compile(
    r"\b(yes|yeah|yep|sure|ok|okay|haan|ha|हाँ|हां|ہاں)\b",
    re.IGNORECASE | re.UNICODE,
)

# Words that count as an explicit "no" / decline.
_NO_PATTERN = re.compile(
    r"\b(no|nahi|nope|cancel|nope|नहीं|نہیں)\b",
    re.IGNORECASE | re.UNICODE,
)

_ALL_TOOLS = [
    classify_image,
    get_price,
    ewaste_quote,
    find_recyclers,
    create_pickup,
    update_pickup,
    offer_to_recycler,
]


def _detect_lang(text: str) -> str:
    """Heuristic: if >10 % of characters are Devanagari, call it Hindi."""
    devanagari = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    return "hi" if devanagari / max(len(text), 1) > 0.10 else "en"


def _format_price(low: int | float, high: int | float) -> str:
    """Always return 'estimated Rs X-Y' regardless of how the LLM formats it."""
    return f"estimated Rs {int(low)}-{int(high)}"


def _remove_internal_reasoning(reply: str) -> str:
    """Never send model reasoning markup to a user-facing channel."""
    cleaned = re.sub(r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>", "", reply,
                     flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


class EcoLinkAgent:
    """
    One instance per Lambda cold-start; re-used across warm invocations.
    State is per-user and loaded from DynamoDB on every call.
    """

    def __init__(
        self,
        api_client: EcoLinkAPIClient | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        # Allow dependency injection for tests
        if api_client is not None:
            tools_module.set_client(api_client)
        elif os.environ.get("EcoLink_API_BASE_URL"):
            tools_module.set_client(EcoLinkAPIClient())

        self._dynamodb_resource = dynamodb_resource

        model_id = os.environ.get(
            "STRANDS_MODEL_ID", "us.amazon.nova-pro-v1:0"
        )

        self._agent = Agent(
            model=model_id,
            system_prompt=SYSTEM_PROMPT,
            tools=_ALL_TOOLS,
        )

    # ------------------------------------------------------------------
    # Guardrails (run in Python, not delegated to the LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_confirmation(text: str) -> bool:
        """Return True if the user's text contains an explicit 'yes'."""
        return bool(_YES_PATTERN.search(text))

    @staticmethod
    def _check_decline(text: str) -> bool:
        """Return True if the user's text contains an explicit 'no'."""
        return bool(_NO_PATTERN.search(text))

    @staticmethod
    def _contains_hazardous(items: list[dict[str, Any]]) -> bool:
        return any(it.get("label", "") in HAZARDOUS_LABELS for it in items)

    @staticmethod
    def _safe_dropoff_message(lang: str) -> str:
        if lang == "hi":
            return (
                "यह सामान खतरनाक है। कृपया इसे अधिकृत ई-वेस्ट केंद्र में जमा करें। "
                "मैं नज़दीकी केंद्र दिखा सकती हूँ – क्या आप चाहते हैं?"
            )
        return (
            "This item needs special handling. "
            "Please drop it at an authorised e-waste centre. "
            "Shall I show you the nearest ones?"
        )

    @staticmethod
    def _tool_error_message(lang: str) -> str:
        if lang == "hi":
            return "माफ़ करें, कुछ गड़बड़ हो गई। थोड़ी देर बाद फिर कोशिश करें।"
        return "Sorry, something went wrong on our end. Please try again in a minute."

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def handle(self, user_id: str, text: str, image_url: str | None = None) -> str:
        """
        Process one user message and return the assistant reply.

        Loads and saves DynamoDB state around the LLM call.
        Applies guardrails both before (confirmation gate, hazardous block)
        and after (price formatting) the LLM response.
        """
        # ── 1. Load state ────────────────────────────────────────────────────
        state = ConversationState(
            user_id=user_id,
            dynamodb_resource=self._dynamodb_resource,
        )
        state.load()

        # ── 2. Detect language ───────────────────────────────────────────────
        lang = _detect_lang(text)
        if lang != state.get("lang"):
            state.set("lang", lang)

        # ── 3. Handle confirmation gate ──────────────────────────────────────
        if state.get("pending_pickup") and not state.is_confirmed():
            if self._check_confirmation(text):
                state.set_confirmation(True)
                logger.info(
                    json.dumps(
                        {
                            "event": "confirmation_received",
                            "user_id": user_id,
                        }
                    )
                )
            elif self._check_decline(text):
                state.set("pending_pickup", None)
                state.set_confirmation(False)
                state.save()
                if lang == "hi":
                    return "कोई बात नहीं! जब भी तैयार हों, बताएं।"
                return "No problem! Let me know whenever you're ready."

        # ── 4. Hazardous items guardrail (preemptive check) ──────────────────
        current_items = state.get("items", [])
        if current_items and self._contains_hazardous(current_items):
            # The prompt handles this, but if somehow pickup was requested,
            # short-circuit here to prevent the LLM bypassing the rule.
            if any(
                kw in text.lower()
                for kw in ("pickup", "book", "schedule", "collect", "बुक", "pickip")
            ):
                state.save()
                return self._safe_dropoff_message(lang)

        # ── 5. Build context string for the agent ───────────────────────────
        context_parts: list[str] = []
        if image_url:
            context_parts.append(f"[IMAGE_URL: {image_url}]")
        if state.get("path"):
            context_parts.append(f"[CURRENT_PATH: {state.get('path')}]")
        if state.get("items"):
            context_parts.append(f"[CLASSIFIED_ITEMS: {json.dumps(state.get('items'))}]")
        if state.is_confirmed():
            context_parts.append("[USER_CONFIRMED: true]")
        if state.get("pending_pickup"):
            context_parts.append(f"[PENDING_PICKUP: {json.dumps(state.get('pending_pickup'))}]")

        context_prefix = "\n".join(context_parts)
        full_input = f"{context_prefix}\n{text}".strip() if context_prefix else text

        logger.info(
            json.dumps(
                {
                    "event": "agent_invoke",
                    "user_id": user_id,
                    "lang": lang,
                    "has_image": bool(image_url),
                    "path": state.get("path"),
                    "confirmed": state.is_confirmed(),
                }
            )
        )

        # ── 6. Run the Strands agent ─────────────────────────────────────────
        try:
            response = self._agent(full_input)
            reply = _remove_internal_reasoning(str(response))
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "agent_error",
                        "user_id": user_id,
                        "error": str(exc),
                    }
                )
            )
            state.save()
            return self._tool_error_message(lang)

        # ── 7. Post-processing guardrail: price formatting ───────────────────
        # Normalise any raw "Rs X" or "₹ X" patterns the LLM might emit
        # to the required "estimated Rs X-Y" format (best-effort regex).
        reply = re.sub(
            r"(?<!\bestimated )(?:Rs\.?|₹)\s*(\d[\d,]*)\s*[-–to]+\s*(\d[\d,]*)",
            lambda m: f"estimated Rs {m.group(1).replace(',', '')}-{m.group(2).replace(',', '')}",
            reply,
        )

        # ── 8. Save state ────────────────────────────────────────────────────
        # After a confirmed pickup is created, reset transient state.
        if state.is_confirmed() and state.get("pickup_id"):
            state.reset()
        else:
            state.save()

        return reply
