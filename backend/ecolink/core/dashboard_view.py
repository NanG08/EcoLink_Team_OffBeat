"""Operations-dashboard views of pickups.

Starts from offers.pickup_view (which strips the address and household identity for
non-admins before confirmation) and adds the derived fields the console renders:
recycler_name, estimated_value, action_log and the household's map position.

ADDRESS RULE: the household coordinates identify the home just as the address does, so
`lat`/`lng` follow the same visibility as `address_text`. When hidden, the dashboard gets
the placeholders it already displays ("[Hidden until confirmed]", "User (protected)").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ecolink.core.offers import address_visible, pickup_view

HIDDEN_ADDRESS = "[Hidden until confirmed]"
HIDDEN_USER = "User (protected)"


def estimated_value(items: list[dict[str, Any]], scrap_rates: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Sum of low/high scrap estimates for items that have a weight and a configured rate."""
    low = high = 0
    counted = False
    for item in items or []:
        rate = scrap_rates.get(item.get("label", ""))
        if rate and item.get("weight_kg"):
            low += round(float(rate["low_per_kg"]) * float(item["weight_kg"]))
            high += round(float(rate["high_per_kg"]) * float(item["weight_kg"]))
            counted = True
    return {"low": low, "high": high, "currency": "INR"} if counted else None


def action_log(pickup: dict[str, Any]) -> list[dict[str, str]]:
    log = []
    for entry in pickup.get("status_history") or []:
        try:
            clock = datetime.fromisoformat(str(entry["at"])).strftime("%H:%M:%S")
        except (KeyError, ValueError):
            clock = str(entry.get("at", ""))
        status = str(entry.get("status", ""))
        log.append({"time": clock, "event": "status_transition", "detail": f"Status updated to {status.upper()}"})
    return log


def build_views(repo: Any, pickups: list[dict[str, Any]], *, privileged: bool) -> list[dict[str, Any]]:
    recyclers: dict[str, dict[str, Any]] = {}
    users: dict[str, dict[str, Any]] = {}
    scrap_rates: dict[str, dict[str, Any]] = {}

    def recycler(rid: str) -> dict[str, Any]:
        if rid not in recyclers:
            recyclers[rid] = repo.get_recycler(rid) or {}
        return recyclers[rid]

    def user(uid: str) -> dict[str, Any]:
        if uid not in users:
            users[uid] = repo.get_user(uid) or {}
        return users[uid]

    for label in {it.get("label") for p in pickups for it in p.get("items") or []}:
        rate = repo.get_scrap_rate(label) if label else None
        if rate:
            scrap_rates[label] = rate

    views = []
    for pickup in pickups:
        show_private = privileged or address_visible(pickup.get("status"))
        view = pickup_view(pickup, privileged=privileged)
        if pickup.get("recycler_id"):
            view["recycler_name"] = recycler(pickup["recycler_id"]).get("name", pickup["recycler_id"])
        value = estimated_value(pickup.get("items"), scrap_rates)
        if value:
            view["estimated_value"] = value
        view["action_log"] = action_log(pickup)
        if show_private:
            household = user(pickup["user_id"])
            if household.get("lat") is not None and household.get("lng") is not None:
                view["lat"], view["lng"] = household["lat"], household["lng"]
        else:
            view["address_text"] = HIDDEN_ADDRESS
            view["user_id"] = HIDDEN_USER
        views.append(view)
    return views
