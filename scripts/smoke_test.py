#!/usr/bin/env python
"""End-to-end check of a DEPLOYED EcoLink stack: real API Gateway, Lambdas, DynamoDB, S3,
Bedrock and the agent. Nothing is mocked.

    python scripts/smoke_test.py             # API + data + Bedrock + agent checks
    python scripts/smoke_test.py --call      # also rings RECYCLER_PHONE and waits for 1 or 2

It finds the API URL and API key itself (CloudFormation outputs + Secrets Manager), so it
only needs AWS credentials. Run scripts/seed.py first. Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import envfile  # noqa: E402
from ecolink.schema import DEFAULT_PREFIX, table_name  # noqa: E402

BASE_LAT, BASE_LNG = 12.9716, 77.5946  # matches scripts/demo_data.py
LABELS = {"paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"}


class Api:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base, self.key = base_url.rstrip("/"), api_key

    def call(self, method: str, path: str, body: Any = None, *, auth: bool = True,
             query: dict | None = None, timeout: int = 60) -> tuple[int, Any]:
        url = self.base + path + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        try:
            return status, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return status, raw.decode("utf-8", "replace")


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    w = h = 32
    raw = b"".join(b"\x00" + b"\x80\xc0\x60" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def multipart_post(url: str, fields: dict[str, str], filename: str, data: bytes, content_type: str) -> tuple[int, str]:
    """POST a file to a pre-signed S3 URL. Returns (status, response body) so errors are visible."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f"Content-Type: {content_type}\r\n\r\n").encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def need(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> int:
    envfile.load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stack", default="EcoLinkBackend")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or "ap-south-1")
    parser.add_argument("--prefix", default=os.environ.get("ECOLINK_PREFIX") or DEFAULT_PREFIX)
    parser.add_argument("--call", action="store_true", help="place a real voice call to RECYCLER_PHONE")
    parser.add_argument("--wait", type=int, default=120, help="seconds to wait for the keypad press (default 120)")
    args = parser.parse_args()

    import boto3

    cfn = boto3.client("cloudformation", region_name=args.region)
    outputs = {o["OutputKey"]: o["OutputValue"]
               for o in cfn.describe_stacks(StackName=args.stack)["Stacks"][0].get("Outputs", [])}
    if os.environ.get("SECRETS_BACKEND", "secretsmanager").lower() == "ssm":
        key = boto3.client("ssm", region_name=args.region).get_parameter(
            Name="/" + outputs["ApiKeySecretName"].lstrip("/"), WithDecryption=True)["Parameter"]["Value"].strip()
    else:
        key = boto3.client("secretsmanager", region_name=args.region).get_secret_value(
            SecretId=outputs["ApiKeySecretName"])["SecretString"].strip()
    api = Api(outputs["ApiUrl"], key)
    pickups = boto3.resource("dynamodb", region_name=args.region).Table(table_name("pickups", args.prefix))
    print(f"stack {args.stack} in {args.region}\napi   {api.base}\n")

    state: dict[str, Any] = {}

    # ── checks ─────────────────────────────────────────────────────────────────
    def auth_is_enforced():
        status, _ = api.call("GET", "/price", auth=False, query={"label": "paper", "weight_kg": 1})
        need(status in (401, 403), f"expected 401/403 without a key, got {status}")
        status, _ = api.call("GET", "/price", query={"label": "paper", "weight_kg": 1})
        need(status == 200, f"expected 200 with the key, got {status}")

    def recycler_match():
        status, rows = api.call("GET", "/recyclers/match", query={
            "lat": BASE_LAT, "lng": BASE_LNG, "labels": "paper,plastic"})
        need(status == 200 and rows, f"no recyclers returned ({status}); did you run scripts/seed.py?")
        need(set(rows[0]) == {"recycler_id", "name", "distance_km", "accepts"}, f"wrong shape: {rows[0]}")
        distances = [r["distance_km"] for r in rows]
        need(distances == sorted(distances), "not sorted by distance")
        need("R008" not in [r["recycler_id"] for r in rows], "unavailable recycler R008 was returned")
        state["recycler"] = rows[0]["recycler_id"]

    def dropoff_points_for_batteries():
        _, rows = api.call("GET", "/recyclers/match", query={"lat": BASE_LAT, "lng": BASE_LNG, "labels": "battery"})
        need(rows and all("battery" in r["accepts"] for r in rows), f"battery drop-off points missing: {rows}")

    def scrap_price():
        status, body = api.call("GET", "/price", query={"label": "paper", "weight_kg": 3})
        need(status == 200 and body["currency"] == "INR" and 0 < body["low"] <= body["high"], f"bad price: {body}")

    def ewaste_quotes():
        status, body = api.call("POST", "/ewaste/quote", {"item_type": "laptop", "age_years": 2, "condition": "working"})
        need(status == 200 and body["recommendation"] == "sell", f"working laptop should sell: {body}")
        _, body = api.call("POST", "/ewaste/quote", {"item_type": "battery", "age_years": 1, "condition": "working"})
        need(body["recommendation"] == "dropoff", f"batteries must be dropoff: {body}")

    def hazardous_pickup_is_blocked():
        status, body = api.call("POST", "/pickups", {
            "user_id": "smoke-test", "items": [{"label": "battery"}], "address_text": "x", "slot": "2026-10-05T10:00:00+05:30"})
        need(status == 422 and body["error"] == "hazardous_items_blocked", f"{status} {body}")

    def upload_then_classify():
        status, up = api.call("POST", "/uploads/presign", {"user_id": "smoke-test", "content_type": "image/png"})
        need(status == 200, f"presign failed: {status} {up}")
        status, answer = multipart_post(up["upload_url"], up["fields"], "t.png", tiny_png(), "image/png")
        need(status in (200, 201, 204),
             f"S3 rejected the pre-signed upload: HTTP {status} at {up['upload_url']} -> {answer[:400]}")
        status, body = api.call("POST", "/classify", {"image_url": up["image_url"]})
        need(status == 200, f"classify failed: {status} {body}")
        need(all(i["label"] in LABELS and 0 <= i["confidence"] <= 1 for i in body["items"]), f"bad items: {body}")
        print(f"      (Bedrock saw a plain green square and returned {body['items']})")

    def pickup_lifecycle_and_state_machine():
        status, body = api.call("POST", "/pickups", {
            "user_id": os.environ.get("DEMO_USER_ID", "smoke-test"), "items": [{"label": "paper", "weight_kg": 2}],
            "address_text": "Smoke Test Flat 1, Test Road", "slot": "2026-10-05T10:00:00+05:30"})
        need(status == 201 and body["pickup_id"], f"create failed: {status} {body}")
        pid = body["pickup_id"]
        status, body = api.call("PATCH", f"/pickups/{pid}", {"status": "completed"})
        need(status == 409, f"requested->completed must be 409, got {status}")
        status, _ = api.call("PATCH", f"/pickups/{pid}", {"status": "confirmed"})
        need(status == 409, f"confirming without an offer must be 409, got {status}")
        status, body = api.call("PATCH", f"/pickups/{pid}", {"status": "cancelled"})
        need(status == 200 and body["status"] == "cancelled", f"cancel failed: {status} {body}")
        need(pickups.get_item(Key={"pickup_id": pid})["Item"]["status"] == "cancelled", "DynamoDB not updated")

    def voice_callback_is_protected():
        status, _ = api.call("POST", "/voice/response", {"pickup_id": "pk_nope", "digit": "1"}, auth=False)
        need(status == 403, f"unsigned callback must be 403, got {status}")
        status, _ = api.call("POST", "/voice/response", {"pickup_id": "pk_nope", "digit": "1"})
        need(status == 404, f"unknown pickup must be 404 with the key, got {status}")

    def agent_replies():
        status, body = api.call("POST", "/agent/message", {
            "user_id": "smoke-test-user", "text": "Hello", "lang": "en"}, timeout=60)
        need(status == 200 and body["reply_text"].strip(), f"agent failed: {status} {body}")
        text = body["reply_text"].lower()
        need("something went wrong" not in text and "couldn't process" not in text and "[stub]" not in text,
             f"the agent answered with its error fallback ({body['reply_text'][:80]!r}); check the agent's CloudWatch logs")
        print(f"      (agent said: {body['reply_text'][:100]!r})")

    def live_call():
        phone = os.environ.get("RECYCLER_PHONE")
        user = os.environ.get("DEMO_USER_ID")
        need(phone and user, "RECYCLER_PHONE and DEMO_USER_ID must be set in .env for --call")
        _, body = api.call("POST", "/pickups", {
            "user_id": user, "items": [{"label": "paper", "weight_kg": 2}],
            "address_text": "Flat 4B, Rosewood Apartments, 12 MG Road, Bengaluru", "slot": "2026-10-05T10:00:00+05:30"})
        pid = body["pickup_id"]
        status, body = api.call("POST", "/voice/offer", {"pickup_id": pid, "recycler_id": state.get("recycler", "R001")})
        need(status == 200 and body["status"] == "offered", f"offer failed: {status} {body}")
        item = pickups.get_item(Key={"pickup_id": pid})["Item"]
        need("Rosewood" not in json.dumps(body), "offer response leaked the address")
        print(f"      phone {phone} is ringing... press 1 to accept or 2 to decline (waiting {args.wait}s)")
        deadline = time.time() + args.wait
        while time.time() < deadline:
            item = pickups.get_item(Key={"pickup_id": pid})["Item"]
            if item["status"] in ("confirmed", "declined"):
                print(f"      keypad result: {item['status']}"
                      + ("  (check WhatsApp: recycler and household should both get a message)" if item["status"] == "confirmed" else ""))
                return
            time.sleep(3)
        raise AssertionError(f"no keypad response within {args.wait}s (status still {item['status']})")

    checks: list[tuple[str, Callable[[], None]]] = [
        ("API key is enforced", auth_is_enforced),
        ("recyclers/match: filtered, sorted, availability", recycler_match),
        ("recyclers/match: battery drop-off points", dropoff_points_for_batteries),
        ("price: scrap estimate", scrap_price),
        ("ewaste/quote: sell vs dropoff", ewaste_quotes),
        ("pickups: hazardous items blocked", hazardous_pickup_is_blocked),
        ("uploads -> S3 -> classify (Bedrock)", upload_then_classify),
        ("pickups: create / state machine / DynamoDB", pickup_lifecycle_and_state_machine),
        ("voice/response: protected", voice_callback_is_protected),
        ("agent/message: real agent reply", agent_replies),
    ]
    if args.call:
        checks.append(("voice: real call + keypad response", live_call))

    failed = 0
    for name, check in checks:
        try:
            check()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}\n      {type(exc).__name__}: {exc}")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
