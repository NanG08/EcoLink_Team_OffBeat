"""/pickups routes.

  POST  /pickups        (API key)  {user_id, items, address_text, slot} -> 201 {pickup_id, status}
  PATCH /pickups/{id}   (API key)  {status}                            -> {pickup_id, status}
  GET   /pickups        (Cognito)  ?status=&user_id=&limit=            -> {pickups: [...]}
  GET   /pickups/{id}   (Cognito)                                      -> pickup
  PATCH /dashboard/pickups/{id} (Cognito, `admins` only)  same body as PATCH /pickups/{id}

Dashboard reads obey the address rule: only members of the `admins` group see the
address / household identity / household location before a pickup is confirmed
(core/dashboard_view.py).
"""

from __future__ import annotations

from typing import Any

from ecolink import deps
from ecolink.core.constants import ALL_STATUSES
from ecolink.core.http import (
    ApiError,
    api_handler,
    bad_request,
    cognito_groups,
    is_cognito_caller,
    json_body,
    not_found,
    path_param,
    query_params,
    respond,
)
from ecolink.core.dashboard_view import build_views
from ecolink.core.validation import parse_number, require_str

ADMIN_GROUP = "admins"


@api_handler
def handler(event: dict, context: Any) -> Any:
    method = event.get("httpMethod")
    pickup_id = path_param(event, "id")
    if method == "POST" and not pickup_id:
        pickup = deps.get_service().create(json_body(event))
        return respond(201, {"pickup_id": pickup["pickup_id"], "status": pickup["status"]})
    if method == "PATCH" and pickup_id:
        if is_cognito_caller(event) and not _privileged(event):
            raise ApiError(403, "forbidden", "Only members of the 'admins' group can change a pickup's status")
        status = require_str(json_body(event), "status", 32)
        pickup = deps.get_service().transition(pickup_id, status)
        return {"pickup_id": pickup["pickup_id"], "status": pickup["status"]}
    if method == "GET" and pickup_id:
        return _get(event, pickup_id)
    if method == "GET":
        return _list(event)
    raise ApiError(405, "method_not_allowed", f"{method} is not supported on this route")


def _privileged(event: dict) -> bool:
    return ADMIN_GROUP in cognito_groups(event)


def _get(event: dict, pickup_id: str) -> dict[str, Any]:
    pickup = deps.get_repo().get_pickup(pickup_id)
    if not pickup:
        raise not_found(f"Pickup '{pickup_id}' not found")
    return build_views(deps.get_repo(), [pickup], privileged=_privileged(event))[0]


def _list(event: dict) -> dict[str, Any]:
    q = query_params(event)
    status = q.get("status")
    if status and status not in ALL_STATUSES:
        raise bad_request(f"'status' must be one of: {', '.join(ALL_STATUSES)}")
    limit = int(parse_number(q.get("limit", 50), "limit", lo=1, hi=100))
    rows = deps.get_repo().list_pickups(status=status, user_id=q.get("user_id"), limit=limit)
    return {"pickups": build_views(deps.get_repo(), rows, privileged=_privileged(event))}
