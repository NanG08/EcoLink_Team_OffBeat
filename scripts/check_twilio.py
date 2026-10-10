#!/usr/bin/env python
"""Check that the Twilio credentials work, both in .env and as stored in AWS.

    python scripts/check_twilio.py

1. Logs in to Twilio's REST API with the SID / Auth Token from .env.
2. Does the same with the copy stored in AWS (Secrets Manager or SSM) that the Lambdas use.
3. Confirms TWILIO_VOICE_FROM is a number on the account.
Secrets are never printed. A wrong Auth Token is the usual reason for 'invalid_signature' in the
webhook logs: Twilio signs every webhook with the token, and the backend verifies it with the stored one.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import envfile  # noqa: E402
import put_secrets  # noqa: E402

API = "https://api.twilio.com/2010-04-01"


def twilio_get(sid: str, token: str, path: str) -> tuple[int, dict]:
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib.request.Request(f"{API}/Accounts/{sid}{path}", headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def stored_credentials(region: str) -> dict:
    import boto3

    name = os.environ.get("TWILIO_SECRET_NAME", "ecolink/twilio")
    if os.environ.get("SECRETS_BACKEND", "secretsmanager").lower() == "ssm":
        raw = boto3.client("ssm", region_name=region).get_parameter(
            Name="/" + name.lstrip("/"), WithDecryption=True)["Parameter"]["Value"]
    else:
        raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=name)["SecretString"]
    return json.loads(raw)


def describe(label: str, creds: dict) -> bool:
    status, body = twilio_get(creds["account_sid"], creds["auth_token"], ".json")
    if status == 200:
        print(f"OK    {label}: Twilio accepts the SID and token ending ...{creds['auth_token'][-4:]} "
              f"(account '{body.get('friendly_name', '?')}', type {body.get('type', '?')}, status {body.get('status', '?')})")
        return True
    print(f"FAIL  {label}: Twilio answered {status} ({body.get('message', 'no message')}). "
          "The Account SID or Auth Token is wrong, or the account is suspended.")
    return False


def main() -> int:
    envfile.load_env()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
    env_creds = put_secrets.read_credentials(dict(os.environ))
    ok = describe(".env values", env_creds)

    try:
        aws_creds = stored_credentials(region)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  could not read the stored secret in {region}: {exc}")
        return 1
    ok_aws = describe(f"secret stored in AWS ({region})", aws_creds)
    if ok and not ok_aws:
        print("      -> .env works but the stored copy does not: run  python scripts/put_secrets.py  again.")
    if not ok and ok_aws:
        print("      -> the stored copy works but .env does not: fix .env before re-running put_secrets.py.")
    if aws_creds.get("auth_token") == env_creds["auth_token"] and aws_creds.get("account_sid") == env_creds["account_sid"]:
        print("OK    .env and the stored secret hold the same SID and token")

    if ok:
        query = urllib.parse.urlencode({"PhoneNumber": env_creds["voice_from"]})
        status, body = twilio_get(env_creds["account_sid"], env_creds["auth_token"], f"/IncomingPhoneNumbers.json?{query}")
        found = status == 200 and body.get("incoming_phone_numbers")
        print(("OK    " if found else "WARN  ") + f"TWILIO_VOICE_FROM {env_creds['voice_from']} "
              + ("is a number on this account" if found else "was not found on this account: voice calls will fail"))
    return 0 if ok and ok_aws else 1


if __name__ == "__main__":
    sys.exit(main())
