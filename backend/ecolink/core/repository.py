"""DynamoDB access for users, recyclers, pickups and the rate table.

Handlers/services talk to this class only; tests substitute an in-memory fake with the
same methods. boto3 is imported lazily so the pure modules stay importable anywhere.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ecolink.core.constants import ALL_STATUSES, REQUESTED


class ConflictError(Exception):
    """A conditional write lost a race (e.g. the pickup already changed status)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_dynamo(value: Any) -> Any:
    """boto3's resource layer rejects float; convert recursively to Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(v) for v in value]
    return value


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value


class Repository:
    def __init__(self, resource: Any | None = None) -> None:
        if resource is None:
            import boto3

            resource = boto3.resource("dynamodb")
        self._ddb = resource
        self._tables: dict[str, Any] = {}

    def _table(self, env_name: str) -> Any:
        if env_name not in self._tables:
            self._tables[env_name] = self._ddb.Table(_env(env_name))
        return self._tables[env_name]

    # ── users ──────────────────────────────────────────────────────────────────
    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._table("USERS_TABLE").get_item(Key={"user_id": user_id}).get("Item")

    def ensure_user(self, user_id: str, lang: str = "en") -> None:
        """Create the user on first contact; leave existing records untouched."""
        from botocore.exceptions import ClientError

        try:
            self._table("USERS_TABLE").put_item(
                Item={"user_id": user_id, "lang": lang, "created_at": utc_now()},
                ConditionExpression="attribute_not_exists(user_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def set_user_location(self, user_id: str, lat: float, lng: float) -> None:
        self._table("USERS_TABLE").update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET lat = :lat, lng = :lng, updated_at = :now",
            ExpressionAttributeValues={
                ":lat": to_dynamo(lat), ":lng": to_dynamo(lng), ":now": utc_now(),
            },
        )

    # ── recyclers ──────────────────────────────────────────────────────────────
    def get_recycler(self, recycler_id: str) -> dict[str, Any] | None:
        return self._table("RECYCLERS_TABLE").get_item(Key={"recycler_id": recycler_id}).get("Item")

    def list_recyclers(self) -> list[dict[str, Any]]:
        """Full scan. Fine for tens of recyclers; add a geohash GSI before scaling up."""
        table = self._table("RECYCLERS_TABLE")
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {}
        while True:
            page = table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    # ── pickups ────────────────────────────────────────────────────────────────
    def create_pickup(
        self, user_id: str, items: list[dict[str, Any]], address_text: str, slot: str
    ) -> dict[str, Any]:
        now = utc_now()
        pickup = {
            "pickup_id": f"pk_{uuid.uuid4().hex[:12]}",
            "user_id": user_id,
            "items": items,
            "address_text": address_text,
            "slot": slot,
            "status": REQUESTED,
            "created_at": now,
            "updated_at": now,
            "status_history": [{"status": REQUESTED, "at": now}],
        }
        self._table("PICKUPS_TABLE").put_item(
            Item=to_dynamo(pickup), ConditionExpression="attribute_not_exists(pickup_id)"
        )
        return pickup

    def get_pickup(self, pickup_id: str) -> dict[str, Any] | None:
        return self._table("PICKUPS_TABLE").get_item(Key={"pickup_id": pickup_id}).get("Item")

    def transition_pickup(
        self,
        pickup_id: str,
        expected_status: str,
        new_status: str,
        *,
        set_fields: dict[str, Any] | None = None,
        remove_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Atomically move a pickup from `expected_status` to `new_status`.

        Raises ConflictError if the stored status is no longer `expected_status`.
        """
        from botocore.exceptions import ClientError

        now = utc_now()
        names = {"#s": "status", "#h": "status_history", "#u": "updated_at"}
        values: dict[str, Any] = {
            ":from": expected_status,
            ":to": new_status,
            ":now": now,
            ":entry": [{"status": new_status, "at": now}],
            ":empty": [],
        }
        sets = ["#s = :to", "#u = :now", "#h = list_append(if_not_exists(#h, :empty), :entry)"]
        for i, (key, val) in enumerate((set_fields or {}).items()):
            names[f"#f{i}"] = key
            values[f":f{i}"] = to_dynamo(val)
            sets.append(f"#f{i} = :f{i}")
        removes = []
        for i, key in enumerate(remove_fields):
            names[f"#r{i}"] = key
            removes.append(f"#r{i}")
        expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
        try:
            resp = self._table("PICKUPS_TABLE").update_item(
                Key={"pickup_id": pickup_id},
                UpdateExpression=expression,
                ConditionExpression="#s = :from",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError(pickup_id) from exc
            raise
        return resp["Attributes"]

    def list_pickups(
        self, *, status: str | None = None, user_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Newest first. Filter by status and/or user_id; with neither, all statuses are merged."""
        table = self._table("PICKUPS_TABLE")

        def query(index: str, key: str, value: str) -> list[dict[str, Any]]:
            return table.query(
                IndexName=index,
                KeyConditionExpression="#k = :v",  # 'status' is a DynamoDB reserved word
                ExpressionAttributeNames={"#k": key},
                ExpressionAttributeValues={":v": value},
                ScanIndexForward=False,
                Limit=limit,
            ).get("Items", [])

        if user_id:
            rows = query("by_user", "user_id", user_id)
            if status:
                rows = [r for r in rows if r.get("status") == status]
        elif status:
            rows = query("by_status", "status", status)
        else:
            rows = [r for s in ALL_STATUSES for r in query("by_status", "status", s)]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    # ── rate table ─────────────────────────────────────────────────────────────
    def _rate(self, kind: str, item: str) -> dict[str, Any] | None:
        return self._table("RATE_TABLE").get_item(Key={"kind": kind, "item": item}).get("Item")

    def get_scrap_rate(self, label: str) -> dict[str, Any] | None:
        return self._rate("scrap", label)

    def get_ewaste_rate(self, item_type: str) -> dict[str, Any] | None:
        return self._rate("ewaste", item_type)

    def get_condition_factor(self, condition: str) -> float | None:
        row = self._rate("ewaste_condition", condition)
        return float(row["factor"]) if row else None
