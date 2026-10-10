"""
Scrap price and e-waste quote functions driven by ml/pricing/rate_table.json.

The arithmetic is NOT re-implemented here: it calls the deployed backend's own
functions (backend/ecolink/core/pricing.py) and validators, so a figure computed
here is identical to what GET /price and POST /ewaste/quote return once the
table is loaded into DynamoDB with ml/pricing/seed_rates.py.

    from ml.pricing import rates
    rates.price_estimate("paper", 3)            # {"low": 30, "high": 42, "currency": "INR", "updated": ...}
    rates.ewaste_quote("laptop", 2, "working")   # {"low": 10500, "high": 24500, "recommendation": "sell"}
    rates.explain_quote("laptop", 2, "working")  # every factor behind that number

All amounts are ESTIMATES in INR.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ecolink.core import pricing as backend_pricing  # noqa: E402
from ecolink.core.constants import HAZARDOUS_LABELS, LABELS  # noqa: E402
from ecolink.core.http import ApiError, bad_request, not_found  # noqa: E402
from ecolink.core.validation import parse_number, require_str  # noqa: E402

RATE_TABLE_PATH = Path(__file__).resolve().parent / "rate_table.json"
REGULAR_LABELS = tuple(label for label in LABELS if label not in HAZARDOUS_LABELS)


class RateTableError(ValueError):
    """rate_table.json is missing a field or holds an impossible value."""


@dataclass(frozen=True)
class RateTable:
    raw: dict[str, Any]

    @property
    def updated(self) -> str:
        return self.raw["updated"]

    @property
    def is_placeholder(self) -> bool:
        return bool(self.raw.get("is_placeholder"))

    def scrap_rate(self, label: str) -> dict[str, Any] | None:
        """Row in the shape backend_pricing.scrap_price() expects."""
        row = self.raw["scrap"].get(label)
        if row is None or label.startswith("_"):
            return None
        return {"low_per_kg": row["low_per_kg"], "high_per_kg": row["high_per_kg"],
                "updated": row.get("updated", self.updated)}

    def ewaste_rate(self, item_type: str) -> dict[str, Any] | None:
        row = self.raw["ewaste"]["items"].get(item_type)
        if row is None:
            return None
        return {"base_low": row["base_low"], "base_high": row["base_high"],
                "yearly_depreciation": row["yearly_depreciation"], "updated": row.get("updated", self.updated)}

    def condition_factor(self, condition: str) -> float | None:
        return self.raw["ewaste"]["condition_factors"].get(condition)

    def dynamo_rows(self) -> list[dict[str, Any]]:
        """Rows for the backend's DynamoDB rate_table (PK kind, SK item; see core/pricing.py)."""
        source = self.raw["source"]
        rows: list[dict[str, Any]] = []
        for label in REGULAR_LABELS:
            r = self.raw["scrap"][label]
            rows.append({"kind": "scrap", "item": label, "low_per_kg": r["low_per_kg"],
                         "high_per_kg": r["high_per_kg"], "updated": r.get("updated", self.updated),
                         "placeholder": bool(r.get("placeholder")), "source": source})
        for item, r in self.raw["ewaste"]["items"].items():
            rows.append({"kind": "ewaste", "item": item, "base_low": r["base_low"], "base_high": r["base_high"],
                         "yearly_depreciation": r["yearly_depreciation"], "updated": r.get("updated", self.updated),
                         "placeholder": bool(r.get("placeholder")), "source": source})
        for condition, factor in self.raw["ewaste"]["condition_factors"].items():
            rows.append({"kind": "ewaste_condition", "item": condition, "factor": factor})
        return rows


def validate(raw: dict[str, Any]) -> RateTable:
    """Check the structure and every value; raise RateTableError listing all problems."""
    problems: list[str] = []
    if raw.get("currency") != "INR":
        problems.append("currency must be 'INR'")
    try:
        if date.fromisoformat(str(raw.get("updated"))) > date.today():
            problems.append("'updated' is in the future")
    except ValueError:
        problems.append("'updated' must be an ISO date (YYYY-MM-DD)")
    if not str(raw.get("source", "")).strip():
        problems.append("'source' note is required")

    scrap = raw.get("scrap", {})
    for label in REGULAR_LABELS:
        row = scrap.get(label)
        if row is None:
            problems.append(f"scrap rate missing for '{label}'")
            continue
        lo, hi = row.get("low_per_kg"), row.get("high_per_kg")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)) or not 0 < lo <= hi:
            problems.append(f"scrap '{label}': need 0 < low_per_kg <= high_per_kg (got {lo}, {hi})")
    for label in scrap:
        if not label.startswith("_") and label not in REGULAR_LABELS:
            problems.append(f"scrap '{label}' is not a priceable label (hazardous or unknown)")
    for label in HAZARDOUS_LABELS:
        if label not in raw.get("not_priced_per_kg", {}):
            problems.append(f"not_priced_per_kg must explain why '{label}' has no per-kg rate")

    ewaste = raw.get("ewaste", {})
    items = ewaste.get("items", {})
    if "other" not in items:
        problems.append("ewaste.items needs an 'other' fallback row")
    for item, row in items.items():
        lo, hi, dep = row.get("base_low"), row.get("base_high"), row.get("yearly_depreciation")
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 <= lo <= hi):
            problems.append(f"ewaste '{item}': need 0 <= base_low <= base_high (got {lo}, {hi})")
        if not (isinstance(dep, (int, float)) and 0 <= dep < 1):
            problems.append(f"ewaste '{item}': yearly_depreciation must be in [0, 1) (got {dep})")
    factors = ewaste.get("condition_factors", {})
    if set(factors) != set(backend_pricing.CONDITIONS):
        problems.append(f"condition_factors keys must be {sorted(backend_pricing.CONDITIONS)}")
    for condition, f in factors.items():
        if not (isinstance(f, (int, float)) and 0 <= f <= 1):
            problems.append(f"condition factor '{condition}' must be in [0, 1] (got {f})")

    if problems:
        raise RateTableError("rate_table.json is invalid:\n  - " + "\n  - ".join(problems))
    return RateTable(raw)


@lru_cache(maxsize=4)
def load(path: str | Path = RATE_TABLE_PATH) -> RateTable:
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh))


# ─────────────────────────────────────────────────────────────────────────────
# GET /price
# ─────────────────────────────────────────────────────────────────────────────

def price_estimate(label: Any, weight_kg: Any, table: RateTable | None = None) -> dict[str, Any]:
    """
    Estimated scrap value: ``{"low", "high", "currency": "INR", "updated"}``.

    Same validation and error codes as the backend's GET /price:
    400 bad label / weight, 422 ``hazardous_label`` for ewaste and battery.
    """
    table = table or load()
    label = str(label or "").strip().lower()
    if label not in LABELS:
        raise bad_request(f"'label' must be one of: {', '.join(LABELS)}")
    weight = parse_number(weight_kg, "weight_kg", lo=0, hi=1000, lo_exclusive=True)
    if label in HAZARDOUS_LABELS:
        raise ApiError(422, "hazardous_label",
                       f"'{label}' has no scrap rate; use POST /ewaste/quote or a safe drop-off point")
    rate = table.scrap_rate(label)
    if not rate:
        raise not_found(f"No rate configured for '{label}'")
    return backend_pricing.scrap_price(rate, weight)


# ─────────────────────────────────────────────────────────────────────────────
# POST /ewaste/quote
# ─────────────────────────────────────────────────────────────────────────────

def explain_quote(item_type: Any, age_years: Any, condition: Any,
                  table: RateTable | None = None) -> dict[str, Any]:
    """
    Transparent breakdown of an e-waste quote: every input, factor and rule
    that produced ``low``/``high``/``recommendation``.
    """
    table = table or load()
    body = {"item_type": item_type, "condition": condition}
    item = backend_pricing.normalize_item_type(require_str(body, "item_type", 80))
    age = parse_number(age_years, "age_years", lo=0, hi=50)
    cond = backend_pricing.normalize_condition(require_str(body, "condition", 40))

    if backend_pricing.is_battery(item):
        return {
            "item_type": item, "age_years": age, "condition": cond,
            "rule": "Batteries and power banks are hazardous: always authorised drop-off, never sold.",
            "low": 0, "high": 0, "recommendation": "dropoff", "estimate": True,
        }

    rate = table.ewaste_rate(item)
    matched = item
    if rate is None:
        rate, matched = table.ewaste_rate("other"), "other"
    factor = table.condition_factor(cond)
    if rate is None or factor is None:
        raise not_found("E-waste rates are not configured in rate_table.json")
    age_factor = max(backend_pricing.MIN_AGE_FACTOR, 1.0 - float(rate["yearly_depreciation"]) * age)
    low, high = backend_pricing.ewaste_estimate(rate, factor, age)
    return {
        "item_type": item,
        "rate_row": matched,
        "age_years": age,
        "condition": cond,
        "base_low": rate["base_low"],
        "base_high": rate["base_high"],
        "yearly_depreciation": rate["yearly_depreciation"],
        "age_factor": round(age_factor, 4),
        "condition_factor": factor,
        "combined_factor": round(age_factor * factor, 4),
        "rounding": "to the nearest Rs 10 when the amount is Rs 100 or more",
        "sell_threshold_inr": backend_pricing.SELL_THRESHOLD_INR,
        "rule": f"'sell' if high >= Rs {backend_pricing.SELL_THRESHOLD_INR}, otherwise 'dropoff'",
        "low": low,
        "high": high,
        "recommendation": backend_pricing.recommend(high),
        "placeholder_rates": bool(table.raw["ewaste"]["items"][matched].get("placeholder")),
        "estimate": True,
    }


def ewaste_quote(item_type: Any, age_years: Any, condition: Any,
                 table: RateTable | None = None) -> dict[str, Any]:
    """Contract response of POST /ewaste/quote: ``{"low", "high", "recommendation"}``."""
    q = explain_quote(item_type, age_years, condition, table)
    return {"low": q["low"], "high": q["high"], "recommendation": q["recommendation"]}
