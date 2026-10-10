"""SECRETS_BACKEND=ssm: secrets and the API key live in SSM Parameter Store SecureStrings.

Used when Secrets Manager is unavailable on an account. Real code paths against emulated SSM.
"""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

import boto3

import put_secrets
from ecolink.core import secrets
from ecolink.handlers import authorizer
from ecolink.providers import twilio
from tests.support import API_KEY, REGION, AwsTestCase
from tests.test_env_and_secrets import GOOD

TWILIO_JSON = {"account_sid": "AC" + "2" * 32, "auth_token": "ssm-token",
               "whatsapp_from": "whatsapp:+1", "voice_from": "+1"}


class SsmBackendTests(AwsTestCase):
    seed_data = False

    def setUp(self):
        super().setUp()
        self.ssm = boto3.client("ssm", region_name=REGION)
        self.ssm.put_parameter(Name="/ecolink/api-key", Value=API_KEY, Type="SecureString")
        self.ssm.put_parameter(Name="/ecolink/twilio", Value=json.dumps(TWILIO_JSON), Type="SecureString")
        patcher = mock.patch.dict(os.environ, {"SECRETS_BACKEND": "ssm", "API_KEY_SECRET_NAME": "ecolink/api-key"})
        patcher.start()
        self.addCleanup(patcher.stop)
        secrets._cache.clear()

    def test_names_are_stored_with_a_leading_slash(self):
        self.assertEqual(secrets.get_secret_string("ecolink/api-key"), API_KEY)
        self.assertEqual(secrets.get_secret_string("/ecolink/api-key"), API_KEY)

    def test_authorizer_checks_the_ssm_stored_key(self):
        arn = "arn:aws:execute-api:ap-south-1:123456789012:abc/prod/POST/pickups"
        policy = authorizer.handler({"authorizationToken": f"Bearer {API_KEY}", "methodArn": arn}, None)
        self.assertEqual(policy["policyDocument"]["Statement"][0]["Effect"], "Allow")
        with self.assertRaises(Exception) as ctx:
            authorizer.handler({"authorizationToken": "Bearer wrong", "methodArn": arn}, None)
        self.assertEqual(str(ctx.exception), "Unauthorized")

    def test_twilio_credentials_load_from_ssm(self):
        with mock.patch.dict(os.environ, {"TWILIO_SECRET_NAME": "ecolink/twilio"}):
            for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "TWILIO_VOICE_FROM"):
                os.environ.pop(var, None)
            self.assertEqual(twilio._credentials()["auth_token"], "ssm-token")

    def test_the_agent_reads_its_api_key_from_ssm(self):
        sys.modules.setdefault("strands", mock.MagicMock())  # api_client itself does not import it
        from agent.api_client import EcoLinkAPIClient

        with mock.patch.dict(os.environ, {"EcoLink_API_KEY_SECRET_NAME": "ecolink/api-key",
                                         "EcoLink_API_BASE_URL": "https://api.test"}):
            os.environ.pop("EcoLink_API_KEY", None)
            self.assertEqual(EcoLinkAPIClient()._api_key, API_KEY)

    def test_unsupported_backend_name_is_rejected_by_put_secrets(self):
        with mock.patch.dict(os.environ, {"SECRETS_BACKEND": "vault", **{k: v for k, v in GOOD.items()}}):
            with self.assertRaises(SystemExit):
                put_secrets.main()


class PutSecretsSsmTests(AwsTestCase):
    seed_data = False

    def test_parameters_are_created_updated_and_the_api_key_is_kept(self):
        ssm = boto3.client("ssm", region_name=REGION)
        self.assertEqual(put_secrets.upsert_parameter(ssm, "ecolink/twilio", "v1"), "created")
        self.assertEqual(put_secrets.upsert_parameter(ssm, "ecolink/twilio", "v2"), "updated")
        self.assertEqual(ssm.get_parameter(Name="/ecolink/twilio", WithDecryption=True)["Parameter"]["Value"], "v2")
        self.assertEqual(put_secrets.upsert_parameter(ssm, "ecolink/api-key", "k1", overwrite=False), "created")
        self.assertEqual(put_secrets.upsert_parameter(ssm, "ecolink/api-key", "k2", overwrite=False), "kept")
        self.assertEqual(ssm.get_parameter(Name="/ecolink/api-key", WithDecryption=True)["Parameter"]["Value"], "k1")
        self.assertEqual(ssm.get_parameter(Name="/ecolink/twilio")["Parameter"]["Type"], "SecureString")

    def test_main_stores_twilio_json_and_generates_an_api_key_without_printing_it(self):
        env = {"SECRETS_BACKEND": "ssm", "AWS_REGION": REGION, "ECOLINK_PREFIX": "EcoLink", **GOOD}
        printed = []
        with mock.patch.dict(os.environ, env), mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            put_secrets.main()
            put_secrets.main()  # second run keeps the existing key
        ssm = boto3.client("ssm", region_name=REGION)
        key = ssm.get_parameter(Name="/ecolink/api-key", WithDecryption=True)["Parameter"]["Value"]
        self.assertGreaterEqual(len(key), 40)
        twilio_json = json.loads(ssm.get_parameter(Name="/ecolink/twilio", WithDecryption=True)["Parameter"]["Value"])
        self.assertEqual(twilio_json["account_sid"], GOOD["TWILIO_ACCOUNT_SID"])
        output = "\n".join(printed)
        self.assertNotIn(key, output)
        self.assertNotIn(GOOD["TWILIO_AUTH_TOKEN"], output)
        self.assertIn("kept API key parameter", output)
