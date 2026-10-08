"""Shared test harness for the backend.

Nothing in the backend is replaced by hand-written fakes:
  * AWS (DynamoDB, S3, SNS+SQS, Secrets Manager) is served by `moto`, an AWS emulator, so the
    real boto3 calls in ecolink.core.repository / storage / notifications / secrets run
    unchanged. Tables are created from ecolink.schema -- the same definitions CDK deploys.
  * The Twilio adapter makes REAL HTTP calls (urllib) to a local HTTP server via
    TWILIO_API_BASE, and inbound webhooks are signed with Twilio's real algorithm.
  * Demo data comes from scripts/seed.py, the same code that seeds a deployed stack.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from unittest import mock
from urllib.parse import parse_qsl, urlencode

import boto3
from moto import mock_aws

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import seed  # noqa: E402  (scripts/seed.py)
from ecolink import deps, schema  # noqa: E402
from ecolink.core import secrets  # noqa: E402
from ecolink.providers import twilio  # noqa: E402

REGION = "ap-south-1"
USER_ID = "whatsapp:+919800000001"
RECYCLER_PHONE = "+919000000001"
HOUSEHOLD_ADDRESS = "Flat 4B, Rosewood Apartments, 12 MG Road, Bengaluru 560001"
SLOT = "2026-10-05T10:00:00+05:30"
TWILIO_TOKEN = "test-auth-token"
API_KEY = "test-api-key-0123456789"


class LocalServer:
    """A tiny real HTTP server. `responder(path, body) -> (status, json)` can be replaced."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responder: Callable[[str, str], tuple[int, dict]] = self._default
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                outer.requests.append({"method": self.command, "path": self.path,
                                       "headers": dict(self.headers), "body": body})
                status, payload = outer.responder(self.path, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _handle

            def log_message(self, *args: Any) -> None:  # keep test output clean
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    @staticmethod
    def _default(path: str, body: str) -> tuple[int, dict]:
        if path.endswith("/Messages.json"):
            return 201, {"sid": "SM" + "a" * 32}
        if path.endswith("/Calls.json"):
            return 201, {"sid": "CA" + "b" * 32}
        if path.endswith("/agent/message"):
            return 200, {"reply_text": "Namaste! Regular waste or e-waste?", "actions": []}
        return 404, {"detail": "not found"}

    def forms(self, suffix: str) -> list[dict[str, str]]:
        """Form bodies Twilio would have received at an endpoint ending with `suffix`."""
        return [dict(parse_qsl(r["body"])) for r in self.requests if r["path"].endswith(suffix)]

    def messages(self) -> list[dict[str, str]]:
        return self.forms("/Messages.json")

    def calls(self) -> list[dict[str, str]]:
        return self.forms("/Calls.json")


class AwsTestCase(unittest.TestCase):
    """Real backend code against emulated AWS + a local Twilio endpoint."""

    seed_data = True

    def setUp(self) -> None:
        self.server = LocalServer()
        self.addCleanup(self.server.close)

        env = {
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": REGION, "AWS_REGION": REGION,
            "MESSAGING_PROVIDER": "twilio", "VOICE_PROVIDER": "twilio",
            "TWILIO_ACCOUNT_SID": "AC" + "0" * 32, "TWILIO_AUTH_TOKEN": TWILIO_TOKEN,
            "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886", "TWILIO_VOICE_FROM": "+14155550100",
            "TWILIO_API_BASE": self.server.url,
            "AGENT_URL": f"{self.server.url}/agent/message",
            "API_KEY_SECRET_NAME": "ecolink/api-key",
            "IMAGES_BUCKET": "ecolink-test-images",
            **{spec.env_var: schema.table_name(key) for key, spec in schema.TABLES.items()},
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("AGENT_FUNCTION_NAME", "PUBLIC_API_BASE_URL", "CORS_ORIGIN"):
            os.environ.pop(var, None)

        aws = mock_aws()
        aws.start()
        self.addCleanup(aws.stop)

        self.ddb = boto3.resource("dynamodb", region_name=REGION)
        for key in schema.TABLES:
            self.ddb.create_table(**schema.boto_create_params(key))

        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="ecolink-test-images", CreateBucketConfiguration={"LocationConstraint": REGION})
        boto3.client("secretsmanager", region_name=REGION).create_secret(Name="ecolink/api-key", SecretString=API_KEY)

        sns = boto3.client("sns", region_name=REGION)
        sqs = boto3.client("sqs", region_name=REGION)
        topic_arn = sns.create_topic(Name="ecolink-pickup-status")["TopicArn"]
        self.queue_url = sqs.create_queue(QueueName="status-events")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=self.queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
        os.environ["STATUS_TOPIC_ARN"] = topic_arn

        secrets._cache.clear()
        deps.reset()
        self.addCleanup(deps.reset)
        self.repo = deps.get_repo()  # the REAL Repository
        if self.seed_data:
            seed.seed_all(self.ddb, prefix=schema.DEFAULT_PREFIX, recycler_phone=RECYCLER_PHONE, user_id=USER_ID)

    # ── helpers ────────────────────────────────────────────────────────────────
    def sns_events(self) -> list[dict[str, Any]]:
        """Messages that really went through SNS -> SQS, oldest first."""
        sqs = boto3.client("sqs", region_name=REGION)
        events: list[dict[str, Any]] = []
        while True:
            resp = sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
            if not resp.get("Messages"):
                return events
            for m in resp["Messages"]:
                events.append(json.loads(json.loads(m["Body"])["Message"]))
                sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=m["ReceiptHandle"])

    def new_pickup(self, items=None, address=HOUSEHOLD_ADDRESS) -> str:
        pickup = deps.get_service().create({
            "user_id": USER_ID, "items": items or [{"label": "paper", "weight_kg": 2}],
            "address_text": address, "slot": SLOT})
        return pickup["pickup_id"]


# ── event builders / invokers ──────────────────────────────────────────────────

def api_event(method: str, resource: str, body: Any = None, query: dict | None = None,
              path_params: dict | None = None, claims: dict | None = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {"domainName": "api.test", "stage": "prod", "path": f"/prod{resource}"}
    if claims is not None:
        ctx["authorizer"] = {"claims": claims}
    return {"httpMethod": method, "resource": resource, "path": resource,
            "body": json.dumps(body) if body is not None else None,
            "queryStringParameters": query, "pathParameters": path_params,
            "headers": {}, "requestContext": ctx}


def twilio_event(resource: str, form: dict[str, str], query: dict | None = None,
                 token: str | None = TWILIO_TOKEN) -> dict[str, Any]:
    """An inbound Twilio webhook, signed the way Twilio signs it (token=None -> unsigned)."""
    event = api_event("POST", resource, query=query)
    event["body"] = urlencode(form)
    if token is not None:
        url = f"https://api.test/prod{resource}" + (f"?{urlencode(query)}" if query else "")
        event["headers"] = {"X-Twilio-Signature": twilio.compute_signature(token, url, list(form.items()))}
    return event


def invoke(handler: Callable, event: dict) -> tuple[int, Any]:
    resp = handler(event, None)
    body = resp.get("body")
    try:
        return resp["statusCode"], json.loads(body) if body else None
    except json.JSONDecodeError:
        return resp["statusCode"], body
