"""
Strands Agents SDK tool definitions for EcoLink.

Each tool is a plain Python function decorated with @tool.
The global `_client` is injected at agent startup and can be
replaced with a mock object during tests.

Structured JSON logging is emitted before every outbound call so
CloudWatch Insights can index tool usage by user_id.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from strands import tool

from agent.api_client import EcoLinkAPIClient, EcoLinkAPIError

logger = logging.getLogger(__name__)

# Module-level client – replace with a mock for tests.
_client: EcoLinkAPIClient | None = None

# Labels that must ALWAYS go to safe drop-off, never a normal pickup.
HAZARDOUS_LABELS = {"battery", "ewaste"}

# One-line reuse/recycle tips per label.
TIPS: dict[str, str] = {
    "paper": "Flatten and bundle paper before handing it over – it fetches a better rate per kg.",
    "cardboard": "Remove tape and staples from cardboard boxes so the recycler can shred them cleanly.",
    "plastic": "Rinse plastic containers before recycling to avoid contamination.",
    "metal": "Separate ferrous (iron/steel) from non-ferrous (aluminium/copper) metal to get the best price.",
    "glass": "Wrap glass in newspaper before pickup to prevent injuries during transport.",
    "ewaste": "Remove personal data from devices before handing them to an authorised e-waste facility.",
    "battery": "Never crush or puncture a battery – take it to an authorised hazardous-waste drop-off point.",
}

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}


def _normalise_slot(slot: str) -> str:
    """Convert common WhatsApp date/time phrasing to the API's ISO 8601 format."""
    try:
        datetime.fromisoformat(slot.replace("Z", "+00:00"))
        return slot
    except ValueError:
        pass

    match = re.search(
        r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]{3,9})"
        r"(?:\s+(?P<year>20\d{2}))?.*?\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
        r"(?P<meridiem>am|pm)\b",
        slot,
        re.IGNORECASE,
    )
    if not match:
        return slot

    month = _MONTHS.get(match.group("month")[:3].lower())
    if month is None:
        return slot
    hour = int(match.group("hour"))
    if match.group("meridiem").lower() == "pm" and hour != 12:
        hour += 12
    elif match.group("meridiem").lower() == "am" and hour == 12:
        hour = 0
    year = int(match.group("year") or datetime.now().year)
    try:
        value = datetime(
            year,
            month,
            int(match.group("day")),
            hour,
            int(match.group("minute") or 0),
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
    except ValueError:
        return slot
    return value.isoformat()


def _log_tool_call(
    tool_name: str, user_id: str, params: dict[str, Any], result: Any, duration_ms: float
) -> None:
    """Emit a structured JSON log line for every tool invocation."""
    entry = {
        "event": "tool_call",
        "tool": tool_name,
        "user_id": user_id,
        "params": params,
        "success": not isinstance(result, dict) or "error" not in result,
        "duration_ms": round(duration_ms, 2),
    }
    logger.info(json.dumps(entry))


def _get_client() -> EcoLinkAPIClient:
    if _client is None:
        raise RuntimeError("Tool client not initialised – call tools.set_client() first.")
    return _client


def set_client(client: EcoLinkAPIClient) -> None:
    """Inject the API client (real or mock). Called by agent startup and test harness."""
    global _client
    _client = client


# ──────────────────────────────────────────────────────────────────────────────
# Tool 1 – classify_image
# ──────────────────────────────────────────────────────────────────────────────

@tool
def classify_image(image_url: str, user_id: str = "unknown") -> dict[str, Any]:
    """
    Classify waste items in an image.

    Args:
        image_url: S3 pre-signed URL or public URL of the image.
        user_id: Caller's user ID (for logging).

    Returns:
        {
          "items": [{"label": str, "confidence": float, "hazardous": bool}],
          "tip": str   # one-line reuse/recycle tip for the first item
        }
        On failure: {"error": str}
    """
    params = {"image_url": image_url, "user_id": user_id}
    t0 = time.monotonic()
    try:
        result = _get_client().classify_image(image_url)
        # Attach a tip for the first item found
        items = result.get("items", [])
        tip = ""
        if items:
            first_label = items[0].get("label", "")
            tip = TIPS.get(first_label, "Sort your waste carefully to make recycling more efficient.")
        out = {**result, "tip": tip}
        _log_tool_call("classify_image", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not classify image: {exc.detail}"}
        _log_tool_call("classify_image", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 2 – get_price
# ──────────────────────────────────────────────────────────────────────────────

@tool
def get_price(label: str, weight_kg: float, user_id: str = "unknown") -> dict[str, Any]:
    """
    Get the estimated resale price for a recyclable item.

    Args:
        label: Item label (paper, cardboard, plastic, metal, glass, ewaste, battery).
        weight_kg: Approximate weight in kilograms.
        user_id: Caller's user ID (for logging).

    Returns:
        {
          "low": int, "high": int, "currency": "INR", "updated": str,
          "formatted": "estimated Rs X-Y"
        }
        On failure: {"error": str}
    """
    params = {"label": label, "weight_kg": weight_kg, "user_id": user_id}
    t0 = time.monotonic()
    try:
        result = _get_client().get_price(label, weight_kg)
        low = result.get("low", 0)
        high = result.get("high", 0)
        out = {**result, "formatted": f"estimated Rs {low}-{high}"}
        _log_tool_call("get_price", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not fetch price: {exc.detail}"}
        _log_tool_call("get_price", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 3 – ewaste_quote
# ──────────────────────────────────────────────────────────────────────────────

@tool
def ewaste_quote(
    item_type: str,
    age_years: int,
    condition: str,
    user_id: str = "unknown",
) -> dict[str, Any]:
    """
    Get a resale or drop-off quote for an e-waste item.

    Args:
        item_type: Type of e-waste (e.g. "laptop", "mobile", "tv").
        age_years: Age of the item in years.
        condition: Condition descriptor (e.g. "working", "broken", "damaged").
        user_id: Caller's user ID (for logging).

    Returns:
        {
          "low": int, "high": int,
          "recommendation": "sell" | "dropoff",
          "formatted": "estimated Rs X-Y"
        }
        On failure: {"error": str}
    """
    params = {
        "item_type": item_type,
        "age_years": age_years,
        "condition": condition,
        "user_id": user_id,
    }
    t0 = time.monotonic()
    try:
        result = _get_client().ewaste_quote(item_type, age_years, condition)
        low = result.get("low", 0)
        high = result.get("high", 0)
        out = {**result, "formatted": f"estimated Rs {low}-{high}"}
        _log_tool_call("ewaste_quote", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not get e-waste quote: {exc.detail}"}
        _log_tool_call("ewaste_quote", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 4 – find_recyclers
# ──────────────────────────────────────────────────────────────────────────────

@tool
def find_recyclers(
    lat: float,
    lng: float,
    labels: list[str],
    user_id: str = "unknown",
) -> dict[str, Any]:
    """
    Find nearby recyclers that accept the given item labels.

    Args:
        lat: User's latitude.
        lng: User's longitude.
        labels: List of item labels to match (e.g. ["paper", "plastic"]).
        user_id: Caller's user ID (for logging).

    Returns:
        {"recyclers": [{recycler_id, name, distance_km, accepts}]}
        On failure: {"error": str}
    """
    params = {"lat": lat, "lng": lng, "labels": labels, "user_id": user_id}
    t0 = time.monotonic()
    try:
        result = _get_client().find_recyclers(lat, lng, labels)
        out = {"recyclers": result}
        _log_tool_call("find_recyclers", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not find recyclers: {exc.detail}"}
        _log_tool_call("find_recyclers", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 5 – create_pickup
# ──────────────────────────────────────────────────────────────────────────────

@tool
def create_pickup(
    user_id: str,
    items: list[dict[str, Any]],
    address_text: str,
    slot: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """
    Book a pickup for recyclable items.

    GUARDRAIL: The 'confirmed' flag must be True, meaning the user has
    explicitly said "yes" before this tool takes effect.
    Hazardous items (battery, ewaste) are blocked here – they must go
    to authorised drop-off points.

    Args:
        user_id: Caller's user ID.
        items: List of item dicts e.g. [{"label": "paper", "weight_kg": 2}].
        address_text: Free-text address for pickup.
        slot: ISO datetime slot string (e.g. "2026-10-05T10:00:00+05:30").
        confirmed: Must be True – set only after explicit user confirmation.

    Returns:
        {"pickup_id": str} on success.
        {"error": str} if guardrail blocks the action.
    """
    params = {
        "user_id": user_id,
        "items": items,
        "address_text": address_text,
        "slot": slot,
        "confirmed": confirmed,
    }
    t0 = time.monotonic()

    # ── Guardrail 1: explicit confirmation required ──────────────────────────
    if not confirmed:
        err = {
            "error": "pickup_not_confirmed",
            "message": "A pickup can only be created after the user explicitly says yes.",
        }
        _log_tool_call("create_pickup", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err

    # ── Guardrail 2: block hazardous items ───────────────────────────────────
    hazardous_found = [
        it["label"] for it in items if it.get("label", "") in HAZARDOUS_LABELS
    ]
    if hazardous_found:
        err = {
            "error": "hazardous_items_blocked",
            "labels": hazardous_found,
            "message": (
                "Batteries and e-waste cannot be collected by a regular kabadiwala. "
                "Please drop them at an authorised e-waste facility."
            ),
        }
        _log_tool_call("create_pickup", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err

    try:
        result = _get_client().create_pickup(user_id, items, address_text, _normalise_slot(slot))
        _log_tool_call("create_pickup", user_id, params, result, (time.monotonic() - t0) * 1000)
        return result
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not create pickup: {exc.detail}"}
        _log_tool_call("create_pickup", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 6 – update_pickup
# ──────────────────────────────────────────────────────────────────────────────

VALID_STATUSES = {"requested", "offered", "confirmed", "completed", "cancelled", "declined"}


@tool
def update_pickup(
    pickup_id: str,
    status: str,
    user_id: str = "unknown",
) -> dict[str, Any]:
    """
    Update the status of an existing pickup.

    Args:
        pickup_id: The pickup ID returned by create_pickup.
        status: One of: requested, offered, confirmed, completed, cancelled, declined.
        user_id: Caller's user ID (for logging).

    Returns:
        {"ok": True} on success.
        {"error": str} on failure.
    """
    params = {"pickup_id": pickup_id, "status": status, "user_id": user_id}
    t0 = time.monotonic()

    if status not in VALID_STATUSES:
        err = {"error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"}
        _log_tool_call("update_pickup", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err

    try:
        result = _get_client().update_pickup(pickup_id, status)
        out = result if result else {"ok": True}
        _log_tool_call("update_pickup", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not update pickup: {exc.detail}"}
        _log_tool_call("update_pickup", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err


# ──────────────────────────────────────────────────────────────────────────────
# Tool 7 – offer_to_recycler
# ──────────────────────────────────────────────────────────────────────────────

@tool
def offer_to_recycler(
    pickup_id: str,
    recycler_id: str,
    user_id: str = "unknown",
) -> dict[str, Any]:
    """
    Trigger a voice call to offer a pickup to a recycler.
    The recycler presses 1 to accept or 2 to decline.
    NOTE: The user's address is shared with the recycler ONLY after acceptance.

    Args:
        pickup_id: The pickup to offer.
        recycler_id: The recycler to call.
        user_id: Caller's user ID (for logging).

    Returns:
        {"ok": True} on success.
        {"error": str} on failure.
    """
    params = {"pickup_id": pickup_id, "recycler_id": recycler_id, "user_id": user_id}
    t0 = time.monotonic()
    try:
        result = _get_client().offer_to_recycler(pickup_id, recycler_id)
        out = result if result else {"ok": True}
        _log_tool_call("offer_to_recycler", user_id, params, out, (time.monotonic() - t0) * 1000)
        return out
    except EcoLinkAPIError as exc:
        err = {"error": f"Could not offer pickup to recycler: {exc.detail}"}
        _log_tool_call("offer_to_recycler", user_id, params, err, (time.monotonic() - t0) * 1000)
        return err
