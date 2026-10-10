"""Price estimation from rows of the rate_table. Pure functions; all prices are estimates.

rate_table key schema: PK `kind` (S), SK `item` (S)
  kind=scrap            item=<label>      low_per_kg, high_per_kg, updated
  kind=ewaste           item=<item_type>  base_low, base_high, yearly_depreciation, updated
  kind=ewaste_condition item=<condition>  factor
"""

from __future__ import annotations

from typing import Any

from ecolink.core.http import bad_request

# Recommend "sell" when the estimated upper bound is at least this many rupees.
SELL_THRESHOLD_INR = 500
# An item never depreciates below this fraction of its base value.
MIN_AGE_FACTOR = 0.05

CONDITIONS = ("working", "damaged", "broken")
_CONDITION_ALIASES = {
    "working": "working", "good": "working", "ok": "working", "fine": "working",
    "damaged": "damaged", "cracked": "damaged", "scratched": "damaged", "partial": "damaged",
    "broken": "broken", "dead": "broken", "faulty": "broken", "not working": "broken",
}

_ITEM_ALIASES = {
    "phone": "mobile", "mobile phone": "mobile", "smartphone": "mobile", "cellphone": "mobile",
    "mobile": "mobile",
    "laptop": "laptop", "notebook": "laptop",
    "tv": "tv", "television": "tv",
    "fridge": "refrigerator", "refrigerator": "refrigerator",
    "ac": "ac", "air conditioner": "ac",
    "washing machine": "washing_machine", "washer": "washing_machine",
    "tablet": "tablet", "ipad": "tablet",
    "monitor": "monitor", "computer monitor": "monitor",
    "printer": "printer",
}


def normalize_item_type(raw: str) -> str:
    key = " ".join(raw.lower().replace("_", " ").split())
    return _ITEM_ALIASES.get(key, key.replace(" ", "_"))


def is_battery(item_type: str) -> bool:
    return "batter" in item_type or "power_bank" in item_type or item_type == "powerbank"


def normalize_condition(raw: str) -> str:
    key = " ".join(raw.lower().split())
    if key not in _CONDITION_ALIASES:
        raise bad_request(f"'condition' must be one of: {', '.join(CONDITIONS)}")
    return _CONDITION_ALIASES[key]


def scrap_price(rate: dict[str, Any], weight_kg: float) -> dict[str, Any]:
    """Estimated scrap value for `weight_kg` of a material."""
    low = max(0, round(float(rate["low_per_kg"]) * weight_kg))
    high = max(low, round(float(rate["high_per_kg"]) * weight_kg))
    return {"low": low, "high": high, "currency": "INR", "updated": str(rate.get("updated", ""))}


def _round_10(value: float) -> int:
    return int(round(value / 10.0)) * 10 if value >= 100 else int(round(value))


def ewaste_estimate(
    rate: dict[str, Any], condition_factor: float, age_years: float
) -> tuple[int, int]:
    age_factor = max(MIN_AGE_FACTOR, 1.0 - float(rate["yearly_depreciation"]) * age_years)
    factor = age_factor * float(condition_factor)
    low = _round_10(float(rate["base_low"]) * factor)
    high = max(low, _round_10(float(rate["base_high"]) * factor))
    return low, high


def recommend(high: int) -> str:
    return "sell" if high >= SELL_THRESHOLD_INR else "dropoff"
