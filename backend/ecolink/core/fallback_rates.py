"""Fallback price table: ml/pricing/rate_table.json, deployed to the models bucket.

GET /price and POST /ewaste/quote read their rates from DynamoDB (the rate_table). If a rate
is missing there (table not seeded yet) or DynamoDB errors, they use this file instead, so an
estimate can still be given. The arithmetic is unchanged (core/pricing.py); only the source of
the numbers differs. Every row is an estimate, and the file's own `updated` date is returned.
"""

from __future__ import annotations

from typing import Any

from ecolink.core.log import log
from ecolink.core.model_store import PRICING_TABLE_KEY, ModelStore, ModelUnavailable

_table: dict[str, Any] | None = None


def _load(store: ModelStore | None = None) -> dict[str, Any] | None:
    global _table
    if _table is None:
        import os

        try:
            _table = (store or ModelStore()).read_json(os.environ.get("PRICING_TABLE_KEY", PRICING_TABLE_KEY))
        except ModelUnavailable as exc:
            log("fallback_rates_unavailable", error=str(exc))
            return None
    return _table


def reset() -> None:
    global _table
    _table = None


def scrap_rate(label: str) -> dict[str, Any] | None:
    table = _load()
    row = (table or {}).get("scrap", {}).get(label)
    if not row or label.startswith("_"):
        return None
    return {"low_per_kg": row["low_per_kg"], "high_per_kg": row["high_per_kg"],
            "updated": row.get("updated", table.get("updated", ""))}


def ewaste_rate(item_type: str) -> dict[str, Any] | None:
    table = _load()
    row = ((table or {}).get("ewaste", {}).get("items") or {}).get(item_type)
    if not row:
        return None
    return {"base_low": row["base_low"], "base_high": row["base_high"],
            "yearly_depreciation": row["yearly_depreciation"], "updated": row.get("updated", table.get("updated", ""))}


def condition_factor(condition: str) -> float | None:
    factor = ((_load() or {}).get("ewaste", {}).get("condition_factors") or {}).get(condition)
    return float(factor) if factor is not None else None
