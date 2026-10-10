"""API Gateway TOKEN authorizer: `Authorization: Bearer <key>` checked against a Secrets
Manager secret (API_KEY_SECRET_NAME). The agent's api_client already sends this header.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from ecolink.core.log import log
from ecolink.core.secrets import get_secret_string


def _policy(effect: str, method_arn: str) -> dict[str, Any]:
    # The result is cached per token, so allow the whole stage rather than one method.
    arn_parts = method_arn.split(":")
    api_id, stage = arn_parts[5].split("/")[:2]
    resource = ":".join(arn_parts[:5]) + f":{api_id}/{stage}/*/*"
    return {
        "principalId": "ecolink-api-client",
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}],
        },
    }


def token_is_valid(authorization_header: str) -> bool:
    """True if `Authorization: Bearer <key>` matches the API key in Secrets Manager."""
    scheme, _, presented = (authorization_header or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    try:
        expected = get_secret_string(os.environ["API_KEY_SECRET_NAME"]).strip()
    except Exception as exc:  # noqa: BLE001
        log("authorizer_secret_error", error=str(exc))
        return False
    return bool(expected) and hmac.compare_digest(presented.encode(), expected.encode())


def handler(event: dict, context: Any) -> dict[str, Any]:
    if not token_is_valid(event.get("authorizationToken") or ""):
        log("authorizer_denied")
        raise Exception("Unauthorized")  # noqa: TRY002 - API Gateway maps this to 401
    return _policy("Allow", event["methodArn"])
