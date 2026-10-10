"""GET /price?label=&weight_kg=  and  POST /ewaste/quote {item_type, age_years, condition}.

All amounts are ESTIMATES in INR, computed from the rate_table.
"""

from __future__ import annotations

from typing import Any

from ecolink import deps
from ecolink.core import pricing
from ecolink.core.constants import HAZARDOUS_LABELS, LABELS
from ecolink.core.http import ApiError, api_handler, bad_request, json_body, not_found, query_params
from ecolink.core.validation import parse_number, require_str


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    route = event.get("resource") or event.get("path", "")
    if route.endswith("/price") and event.get("httpMethod") == "GET":
        return _price(event)
    if route.endswith("/ewaste/quote") and event.get("httpMethod") == "POST":
        return _ewaste_quote(event)
    raise ApiError(404, "not_found", "Unknown route")


def _price(event: dict) -> dict[str, Any]:
    q = query_params(event)
    label = str(q.get("label", "")).strip().lower()
    if label not in LABELS:
        raise bad_request(f"'label' must be one of: {', '.join(LABELS)}")
    weight = parse_number(q.get("weight_kg"), "weight_kg", lo=0, hi=1000, lo_exclusive=True)
    if label in HAZARDOUS_LABELS:
        raise ApiError(
            422,
            "hazardous_label",
            f"'{label}' has no scrap rate; use POST /ewaste/quote or a safe drop-off point",
        )
    rate = deps.get_repo().get_scrap_rate(label)
    if not rate:
        raise not_found(f"No rate configured for '{label}'")
    return pricing.scrap_price(rate, weight)


def _ewaste_quote(event: dict) -> dict[str, Any]:
    data = json_body(event)
    item_type = pricing.normalize_item_type(require_str(data, "item_type", 80))
    age = parse_number(data.get("age_years"), "age_years", lo=0, hi=50)
    condition = pricing.normalize_condition(require_str(data, "condition", 40))

    if pricing.is_battery(item_type):  # batteries ALWAYS go to safe drop-off
        return {"low": 0, "high": 0, "recommendation": "dropoff"}

    repo = deps.get_repo()
    rate = repo.get_ewaste_rate(item_type) or repo.get_ewaste_rate("other")
    factor = repo.get_condition_factor(condition)
    if not rate or factor is None:
        raise not_found("E-waste rates are not configured; run scripts/seed.py")
    low, high = pricing.ewaste_estimate(rate, factor, age)
    return {"low": low, "high": high, "recommendation": pricing.recommend(high)}
