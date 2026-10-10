"""GET /price?label=&weight_kg=  and  POST /ewaste/quote {item_type, age_years, condition}.

All amounts are ESTIMATES in INR, computed from the rate_table in DynamoDB. If a rate is
missing there (or DynamoDB fails) the rate table deployed to the models bucket is used instead
(core/fallback_rates.py), so an estimate is still returned.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ecolink import deps
from ecolink.core import fallback_rates, pricing
from ecolink.core.constants import HAZARDOUS_LABELS, LABELS
from ecolink.core.http import ApiError, api_handler, bad_request, json_body, not_found, query_params
from ecolink.core.log import log
from ecolink.core.validation import parse_number, require_str


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    route = event.get("resource") or event.get("path", "")
    if route.endswith("/price") and event.get("httpMethod") == "GET":
        return _price(event)
    if route.endswith("/ewaste/quote") and event.get("httpMethod") == "POST":
        return _ewaste_quote(event)
    raise ApiError(404, "not_found", "Unknown route")


def _lookup(primary: Callable[[str], Any], fallback: Callable[[str], Any], key: str) -> Any:
    """DynamoDB rate first; the table from the models bucket when DynamoDB has no row or fails."""
    try:
        value = primary(key)
    except Exception:  # noqa: BLE001 - DynamoDB outage / throttling must not stop an estimate
        logging.getLogger("ecolink").exception("rate_lookup_failed key=%s", key)
        value = None
    if value is None or value == {}:
        value = fallback(key)
        if value is not None:
            log("rate_from_fallback_table", key=key)
    return value


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
    rate = _lookup(deps.get_repo().get_scrap_rate, fallback_rates.scrap_rate, label)
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
    rate = (_lookup(repo.get_ewaste_rate, fallback_rates.ewaste_rate, item_type)
            or _lookup(repo.get_ewaste_rate, fallback_rates.ewaste_rate, "other"))
    factor = _lookup(repo.get_condition_factor, fallback_rates.condition_factor, condition)
    if not rate or factor is None:
        raise not_found("E-waste rates are not configured; run scripts/seed.py")
    low, high = pricing.ewaste_estimate(rate, factor, age)
    return {"low": low, "high": high, "recommendation": pricing.recommend(high)}
