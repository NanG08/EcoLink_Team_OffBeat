"""GET /recyclers/match?lat=&lng=&labels=paper,plastic[&mode=all|any][&limit=5][&max_km=]

-> [{recycler_id, name, distance_km, accepts}], nearest first. No address of any kind is
involved here; households are only ever referred to by coordinates the caller sends.
"""

from __future__ import annotations

from typing import Any

from ecolink import deps
from ecolink.core.http import api_handler, bad_request, query_params
from ecolink.core.matching import MODE_ALL, MODE_ANY, match_recyclers
from ecolink.core.validation import parse_labels, parse_number


@api_handler
def handler(event: dict, context: Any) -> list[dict[str, Any]]:
    q = query_params(event)
    lat = parse_number(q.get("lat"), "lat", lo=-90, hi=90)
    lng = parse_number(q.get("lng"), "lng", lo=-180, hi=180)
    labels = parse_labels(q.get("labels"))
    mode = q.get("mode", MODE_ALL).lower()
    if mode not in (MODE_ALL, MODE_ANY):
        raise bad_request(f"'mode' must be '{MODE_ALL}' or '{MODE_ANY}'")
    limit = int(parse_number(q.get("limit", 5), "limit", lo=1, hi=20))
    max_km = parse_number(q["max_km"], "max_km", lo=0, hi=20000) if q.get("max_km") else None

    return match_recyclers(
        deps.get_repo().list_recyclers(), lat, lng, labels, mode=mode, max_km=max_km, limit=limit
    )
