"""POST /auth/login {email, password[, new_password]} -> {id_token, expires_in, user, group}

Signs a dashboard user in against the Cognito user pool (USER_PASSWORD_AUTH) and returns
the ID token the dashboard sends to the Cognito-protected routes. `group` is "admins" or
"viewers". Users created by an admin start with a temporary password: the first login then
answers 409 `new_password_required`; repeat it with `new_password` to set a permanent one.
Cognito itself throttles and locks out repeated failures.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from ecolink.core.http import ApiError, api_handler, json_body
from ecolink.core.validation import optional_str, require_str


def _claims(id_token: str) -> dict[str, Any]:
    """Read the payload of a token Cognito just issued to us (no signature check needed)."""
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    data = json_body(event)
    email = require_str(data, "email", 256)
    password = require_str(data, "password", 256)
    new_password = optional_str(data, "new_password", 256)

    import boto3

    client = boto3.client("cognito-idp")
    client_id = os.environ["COGNITO_CLIENT_ID"]
    try:
        resp = client.initiate_auth(
            ClientId=client_id, AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password})
        if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
            if not new_password:
                raise ApiError(409, "new_password_required",
                               "First sign-in: send 'new_password' along with the temporary password")
            resp = client.respond_to_auth_challenge(
                ClientId=client_id, ChallengeName="NEW_PASSWORD_REQUIRED", Session=resp["Session"],
                ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password})
        if resp.get("ChallengeName"):
            raise ApiError(409, "unsupported_challenge",
                           f"Sign-in needs a '{resp['ChallengeName']}' step this endpoint does not support")
    except (client.exceptions.NotAuthorizedException, client.exceptions.UserNotFoundException):
        raise ApiError(401, "invalid_credentials", "Incorrect email or password") from None
    except client.exceptions.UserNotConfirmedException:
        raise ApiError(403, "user_not_confirmed", "This account has not been confirmed yet") from None
    except client.exceptions.InvalidPasswordException as exc:
        raise ApiError(400, "invalid_new_password", str(exc)) from None

    result = resp["AuthenticationResult"]
    claims = _claims(result["IdToken"])
    groups = claims.get("cognito:groups") or []
    return {
        "id_token": result["IdToken"],
        "expires_in": result.get("ExpiresIn", 3600),
        "user": {"email": claims.get("email", email), "name": str(claims.get("email", email)).split("@")[0]},
        "group": "admins" if "admins" in groups else "viewers",
    }
