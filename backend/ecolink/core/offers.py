"""The ADDRESS SHARING RULE lives here, in one place.

A recycler (or any non-admin viewer) must NOT receive the household address until
the pickup status is `confirmed`. Every payload that can reach a recycler -- the
voice-offer payload, the confirmation message, dashboard views -- is built by the
whitelist builders below, never by copying the pickup record. Adding a new field to
a recycler-facing payload therefore requires an explicit edit here.
"""

from __future__ import annotations

from typing import Any

from ecolink.core.constants import ADDRESS_VISIBLE_STATUSES

ADDRESS_FIELD = "address_text"


class AddressLeakError(RuntimeError):
    """Raised if a recycler-facing payload would contain the address too early."""


def address_visible(status: str | None) -> bool:
    return status in ADDRESS_VISIBLE_STATUSES


def _items_view(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items or []:
        row: dict[str, Any] = {"label": it.get("label")}
        if it.get("weight_kg") is not None:
            row["weight_kg"] = it["weight_kg"]
        out.append(row)
    return out


def recycler_view(
    pickup: dict[str, Any],
    recycler: dict[str, Any] | None = None,
    distance_km: float | None = None,
) -> dict[str, Any]:
    """What a recycler may see about a pickup. Address only once confirmed."""
    payload: dict[str, Any] = {
        "pickup_id": pickup["pickup_id"],
        "status": pickup["status"],
        "items": _items_view(pickup.get("items")),
        "slot": pickup.get("slot"),
    }
    if recycler is not None:
        payload["recycler_id"] = recycler["recycler_id"]
    if distance_km is not None:
        payload["distance_km"] = round(float(distance_km), 1)
    if address_visible(pickup["status"]):
        payload[ADDRESS_FIELD] = pickup.get(ADDRESS_FIELD)
    return payload


def pickup_view(pickup: dict[str, Any], *, privileged: bool) -> dict[str, Any]:
    """Dashboard view. Non-privileged viewers get the address/household identity only
    once confirmed, exactly like a recycler would."""
    include_private = privileged or address_visible(pickup.get("status"))
    view = {
        k: v
        for k, v in pickup.items()
        if k not in (ADDRESS_FIELD, "user_id") or include_private
    }
    return view


def assert_no_address(payload: dict[str, Any], pickup: dict[str, Any]) -> None:
    """Defence in depth before an offer leaves the system."""
    if ADDRESS_FIELD in payload:
        raise AddressLeakError("Offer payload must not contain the address before confirmation")
    address = (pickup.get(ADDRESS_FIELD) or "").strip()
    if address and address.lower() in repr(payload).lower():
        raise AddressLeakError("Offer payload contains the household address text")
