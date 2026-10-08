"""
DynamoDB-backed conversation state manager for EcoLink.

Table schema (single-table design):
    PK (S) = user_id
    TTL    = epoch seconds, auto-set to 7 days from last update

State fields stored per user:
    path              : "regular" | "ewaste" | None
    items             : list of classified item dicts
    pending_pickup    : dict with address_text, slot, items
    confirmation_flag : bool – True only after user explicitly says "yes"
    pickup_id         : str | None
    recycler_id       : str | None
    lang              : "en" | "hi"
    last_updated      : ISO timestamp string
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Default DynamoDB table name; override via environment variable.
_DEFAULT_TABLE = "EcoLink-conversation-state"
_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


class ConversationState:
    """
    Thin wrapper that reads / writes a single user's conversation state
    in DynamoDB.  Uses optimistic locking via a version counter to avoid
    lost updates when Lambda is invoked concurrently for the same user.
    """

    DEFAULT: dict[str, Any] = {
        "path": None,
        "items": [],
        "pending_pickup": None,
        "confirmation_flag": False,
        "pickup_id": None,
        "recycler_id": None,
        "lang": "en",
        "version": 0,
    }

    def __init__(
        self,
        user_id: str,
        table_name: str | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        self.user_id = user_id
        table_name = table_name or os.environ.get("DYNAMODB_STATE_TABLE", _DEFAULT_TABLE)
        resource = dynamodb_resource or boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        )
        self._table = resource.Table(table_name)
        self._data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Fetch the user's state from DynamoDB. Returns defaults if not found."""
        try:
            resp = self._table.get_item(Key={"user_id": self.user_id})
            item = resp.get("Item")
            if item:
                # DynamoDB stores JSON-serialised complex fields as strings
                for field in ("items", "pending_pickup"):
                    if field in item and isinstance(item[field], str):
                        item[field] = json.loads(item[field])
                self._data = {**self.DEFAULT, **item}
            else:
                self._data = {**self.DEFAULT, "user_id": self.user_id}
        except ClientError as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "state_load_error",
                        "user_id": self.user_id,
                        "error": str(exc),
                    }
                )
            )
            self._data = {**self.DEFAULT, "user_id": self.user_id}
        return self._data

    def save(self) -> None:
        """Persist the current state back to DynamoDB with TTL update."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        ttl = int(time.time()) + _TTL_SECONDS
        version = int(self._data.get("version", 0)) + 1

        item: dict[str, Any] = {
            "user_id": self.user_id,
            "path": self._data.get("path"),
            "items": json.dumps(self._data.get("items", [])),
            "pending_pickup": json.dumps(self._data.get("pending_pickup"))
            if self._data.get("pending_pickup")
            else None,
            "confirmation_flag": self._data.get("confirmation_flag", False),
            "pickup_id": self._data.get("pickup_id"),
            "recycler_id": self._data.get("recycler_id"),
            "lang": self._data.get("lang", "en"),
            "last_updated": now_iso,
            "ttl": ttl,
            "version": version,
        }
        # Remove None values so DynamoDB doesn't store empty strings.
        item = {k: v for k, v in item.items() if v is not None}

        try:
            old_version = int(self._data.get("version", 0))
            if old_version == 0:
                # New item – use attribute_not_exists guard
                self._table.put_item(
                    Item=item,
                    ConditionExpression=Attr("user_id").not_exists(),
                )
            else:
                self._table.put_item(
                    Item=item,
                    ConditionExpression=Attr("version").eq(old_version),
                )
            self._data["version"] = version
            logger.info(
                json.dumps(
                    {
                        "event": "state_saved",
                        "user_id": self.user_id,
                        "version": version,
                    }
                )
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.warning(
                    json.dumps(
                        {
                            "event": "state_version_conflict",
                            "user_id": self.user_id,
                        }
                    )
                )
                # Re-raise so caller can retry
                raise
            logger.error(
                json.dumps(
                    {
                        "event": "state_save_error",
                        "user_id": self.user_id,
                        "error": str(exc),
                    }
                )
            )
            raise

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def set_confirmation(self, value: bool) -> None:
        self._data["confirmation_flag"] = value

    def is_confirmed(self) -> bool:
        return bool(self._data.get("confirmation_flag", False))

    def reset(self) -> None:
        """Clear all transient state (e.g. after a pickup is booked)."""
        self._data = {
            **self.DEFAULT,
            "user_id": self.user_id,
            "lang": self._data.get("lang", "en"),
            "version": self._data.get("version", 0),
        }
