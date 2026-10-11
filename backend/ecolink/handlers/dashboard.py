"""Operations dashboard routes.

Every recycler, including unavailable ones, for the console's dispatch map. Phone numbers
are included for the `admins` group only.
"""

from __future__ import annotations

import json
from typing import Any

from ecolink import deps
from ecolink.core.http import api_handler, cognito_groups

ADMIN_GROUP = "admins"
PUBLIC_FIELDS = ("recycler_id", "name", "lat", "lng", "accepts", "available", "lang")


@api_handler
def handler(event: dict, context: Any) -> Any:
    if event.get("resource", "").endswith("/users"):
        return users(event, context)

    privileged = ADMIN_GROUP in cognito_groups(event)
    rows = []
    for r in sorted(deps.get_repo().list_recyclers(), key=lambda r: r["recycler_id"]):
        row = {k: r[k] for k in PUBLIC_FIELDS if k in r}
        if privileged and r.get("phone"):
            row["phone"] = r["phone"]
        rows.append(row)
    return rows


def users(event: dict, context: Any) -> dict[str, list[dict[str, Any]]]:
    """GET /dashboard/users -> WhatsApp users joined with conversation state."""
    repo = deps.get_repo()
    states = {row["user_id"]: _decode_state(row) for row in repo.list_conversation_states()}
    rows = []
    for user in sorted(repo.list_users(), key=lambda row: row.get("user_id", "")):
        state = states.get(user["user_id"], {})
        rows.append({
            "user_id": user["user_id"],
            "name": user.get("name"),
            "lang": user.get("lang", state.get("lang", "en")),
            "lat": user.get("lat"),
            "lng": user.get("lng"),
            "created_at": user.get("created_at"),
            "conversation_state": state,
        })
    return {"users": rows}


def _decode_state(row: dict[str, Any]) -> dict[str, Any]:
    state = dict(row)
    for field in ("items", "pending_pickup"):
        value = state.get(field)
        if isinstance(value, str):
            try:
                state[field] = json.loads(value)
            except json.JSONDecodeError:
                state[field] = None
    state.pop("user_id", None)
    state.pop("ttl", None)
    state.pop("version", None)
    return state
