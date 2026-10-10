"""GET /dashboard/recyclers (Cognito) -> [{recycler_id, name, lat, lng, accepts, available, lang[, phone]}]

Every recycler, including unavailable ones, for the console's dispatch map. Phone numbers
are included for the `admins` group only.
"""

from __future__ import annotations

from typing import Any

from ecolink import deps
from ecolink.core.http import api_handler, cognito_groups

ADMIN_GROUP = "admins"
PUBLIC_FIELDS = ("recycler_id", "name", "lat", "lng", "accepts", "available", "lang")


@api_handler
def handler(event: dict, context: Any) -> list[dict[str, Any]]:
    privileged = ADMIN_GROUP in cognito_groups(event)
    rows = []
    for r in sorted(deps.get_repo().list_recyclers(), key=lambda r: r["recycler_id"]):
        row = {k: r[k] for k in PUBLIC_FIELDS if k in r}
        if privileged and r.get("phone"):
            row["phone"] = r["phone"]
        rows.append(row)
    return rows
