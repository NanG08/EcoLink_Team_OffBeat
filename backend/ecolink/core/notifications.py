"""SNS notifications for pickup status changes. Messages never contain the address."""

from __future__ import annotations

import json
import os
from typing import Any

from ecolink.core.log import log


class SnsNotifier:
    def __init__(self, topic_arn: str | None = None, client: Any | None = None) -> None:
        self.topic_arn = topic_arn or os.environ.get("STATUS_TOPIC_ARN", "")
        if client is None and self.topic_arn:
            import boto3

            client = boto3.client("sns")
        self._sns = client

    def pickup_status_changed(self, pickup: dict[str, Any], previous_status: str | None) -> None:
        message = {
            "event": "pickup.status_changed",
            "pickup_id": pickup["pickup_id"],
            "status": pickup["status"],
            "previous_status": previous_status,
            "user_id": pickup.get("user_id"),
            "recycler_id": pickup.get("recycler_id"),
            "at": pickup.get("updated_at"),
        }
        if not self._sns:
            log("sns_disabled", **message)
            return
        self._sns.publish(
            TopicArn=self.topic_arn,
            Subject=f"EcoLink pickup {pickup['pickup_id']}: {pickup['status']}"[:99],
            Message=json.dumps(message),
            MessageAttributes={"status": {"DataType": "String", "StringValue": pickup["status"]}},
        )
