"""Recycler matching: filter by accepted labels + availability, sort by distance.

Pure functions (no AWS imports) so they are trivially unit-testable.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

EARTH_RADIUS_KM = 6371.0088

MODE_ALL = "all"  # recycler must accept EVERY requested label (default)
MODE_ANY = "any"  # recycler must accept AT LEAST ONE requested label


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def match_recyclers(
    recyclers: Iterable[dict[str, Any]],
    lat: float,
    lng: float,
    labels: Iterable[str],
    *,
    mode: str = MODE_ALL,
    max_km: float | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return [{recycler_id, name, distance_km, accepts}] nearest first.

    A recycler is kept only if it is available, accepts the requested labels (per
    `mode`) and has usable coordinates. Ties on distance break on recycler_id so the
    order is deterministic. Output keys are exactly those of the API contract.
    """
    if mode not in (MODE_ALL, MODE_ANY):
        raise ValueError(f"mode must be '{MODE_ALL}' or '{MODE_ANY}'")
    wanted = set(labels)
    if not wanted:
        return []

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for r in recyclers:
        if r.get("available") is not True:
            continue
        accepts = set(r.get("accepts") or [])
        if not (wanted <= accepts if mode == MODE_ALL else wanted & accepts):
            continue
        try:
            distance = haversine_km(lat, lng, float(r["lat"]), float(r["lng"]))
        except (KeyError, TypeError, ValueError):
            continue
        if max_km is not None and distance > max_km:
            continue
        scored.append(
            (
                distance,
                str(r["recycler_id"]),
                {
                    "recycler_id": str(r["recycler_id"]),
                    "name": r.get("name", ""),
                    "distance_km": round(distance, 2),
                    "accepts": sorted(accepts),
                },
            )
        )

    scored.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in scored[: max(0, limit)]]
