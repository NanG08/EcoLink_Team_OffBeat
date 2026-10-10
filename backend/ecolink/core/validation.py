"""Small input validators that raise ApiError(400) with a clear message."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ecolink.core.constants import LABELS
from ecolink.core.http import bad_request


def require_str(data: dict[str, Any], key: str, max_len: int = 500) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise bad_request(f"'{key}' is required and must be a non-empty string")
    value = value.strip()
    if len(value) > max_len:
        raise bad_request(f"'{key}' must be at most {max_len} characters")
    return value


def optional_str(data: dict[str, Any], key: str, max_len: int = 2000) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise bad_request(f"'{key}' must be a string of at most {max_len} characters")
    return value.strip()


def parse_number(
    raw: Any, name: str, *, lo: float, hi: float, lo_exclusive: bool = False
) -> float:
    if isinstance(raw, bool) or raw is None or raw == "":
        raise bad_request(f"'{name}' is required and must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise bad_request(f"'{name}' must be a number") from None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise bad_request(f"'{name}' must be a finite number")
    if value < lo or value > hi or (lo_exclusive and value == lo):
        op = ">" if lo_exclusive else ">="
        raise bad_request(f"'{name}' must be {op} {lo:g} and <= {hi:g}")
    return value


def parse_labels(raw: Any) -> list[str]:
    """Accept 'paper,plastic' or ['paper','plastic']; every label must be known."""
    items = raw.split(",") if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise bad_request("'labels' must be a comma-separated list of labels")
    labels = [str(x).strip().lower() for x in items if str(x).strip()]
    if not labels:
        raise bad_request("'labels' is required (e.g. labels=paper,plastic)")
    unknown = sorted(set(labels) - set(LABELS))
    if unknown:
        raise bad_request(
            f"Unknown label(s): {', '.join(unknown)}. Allowed: {', '.join(LABELS)}"
        )
    return list(dict.fromkeys(labels))  # de-duplicate, keep order


def parse_slot(raw: Any) -> str:
    slot = require_str({"slot": raw}, "slot", 64)
    try:
        datetime.fromisoformat(slot.replace("Z", "+00:00"))
    except ValueError:
        raise bad_request(
            "'slot' must be an ISO 8601 datetime, e.g. 2026-10-05T10:00:00+05:30"
        ) from None
    return slot
