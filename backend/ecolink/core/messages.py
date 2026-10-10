"""Short WhatsApp-sized message templates (English / Hindi)."""

from __future__ import annotations

from typing import Any

from ecolink.core.offers import ADDRESS_FIELD


def describe_items(items: list[dict[str, Any]]) -> str:
    parts = []
    for it in items:
        weight = it.get("weight_kg")
        label = it.get("label", "item")
        parts.append(f"{float(weight):g} kg {label}" if weight else str(label))
    return ", ".join(parts) or "items"


def _lang(lang: str | None) -> str:
    return "hi" if lang == "hi" else "en"


def user_pickup_confirmed(lang: str | None, recycler_name: str, slot: str) -> str:
    if _lang(lang) == "hi":
        return (
            f"खुशखबरी! {recycler_name} ने आपका पिकअप स्वीकार कर लिया है। "
            f"वे {slot} के स्लॉट में आएंगे। कीमत अनुमानित है और पिकअप पर तय होगी।"
        )
    return (
        f"Good news! {recycler_name} has accepted your pickup for {slot}. "
        "Prices are estimates and are settled at pickup."
    )


def user_pickup_declined(lang: str | None, recycler_name: str) -> str:
    if _lang(lang) == "hi":
        return f"{recycler_name} इस पिकअप के लिए उपलब्ध नहीं हैं। क्या मैं पास के किसी और कबाड़ीवाले को भेजूँ?"
    return f"{recycler_name} couldn't take this pickup. Shall I try another nearby kabadiwala?"


def recycler_pickup_confirmed(lang: str | None, view: dict[str, Any]) -> str:
    """`view` MUST come from offers.recycler_view of a confirmed pickup."""
    items = describe_items(view.get("items", []))
    address = view.get(ADDRESS_FIELD) or "(not provided)"
    if _lang(lang) == "hi":
        return (
            f"पिकअप {view['pickup_id']} पक्का हुआ। सामान: {items}। स्लॉट: {view.get('slot')}। "
            f"पता: {address}। धन्यवाद!"
        )
    return (
        f"Pickup {view['pickup_id']} confirmed. Items: {items}. Slot: {view.get('slot')}. "
        f"Address: {address}. Thank you!"
    )
