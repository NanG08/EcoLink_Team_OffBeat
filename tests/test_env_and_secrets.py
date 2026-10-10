""".env loader and scripts/put_secrets.py (against emulated Secrets Manager)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import boto3

import envfile
import put_secrets
from tests.support import REGION, AwsTestCase


class EnvFileTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / ".env"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(lambda: (path.unlink(), os.rmdir(directory)))
        return path

    def test_parses_comments_quotes_and_skips_empty_values(self):
        path = self.write('# comment\n\nA_KEY=plain\nB_KEY="quoted value"\nC_KEY=\nD_KEY = spaced\nnot a line\n')
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
                os.environ.pop(name, None)
            applied = envfile.load_env(path)
            self.assertEqual(applied, {"A_KEY": "plain", "B_KEY": "quoted value", "D_KEY": "spaced"})
            self.assertNotIn("C_KEY", os.environ)  # empty AWS keys must not reach boto3
            for name in applied:
                os.environ.pop(name)

    def test_real_environment_wins_over_the_file(self):
        path = self.write("E_KEY=from-file\n")
        with mock.patch.dict(os.environ, {"E_KEY": "from-env"}):
            self.assertEqual(envfile.load_env(path), {})
            self.assertEqual(os.environ["E_KEY"], "from-env")

    def test_missing_file_is_fine(self):
        self.assertEqual(envfile.load_env(Path(tempfile.gettempdir()) / "definitely-missing.env"), {})


GOOD = {
    "TWILIO_ACCOUNT_SID": "AC" + "a1" * 16, "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886", "TWILIO_VOICE_FROM": "+14155550100",
}


class PutSecretsTests(AwsTestCase):
    seed_data = False

    def test_credentials_are_validated(self):
        self.assertEqual(put_secrets.read_credentials(GOOD)["voice_from"], "+14155550100")
        for var in GOOD:
            with self.subTest(missing=var), self.assertRaises(SystemExit) as ctx:
                put_secrets.read_credentials({k: v for k, v in GOOD.items() if k != var})
            self.assertIn(var, str(ctx.exception))
        for var, bad in (("TWILIO_ACCOUNT_SID", "nope"), ("TWILIO_WHATSAPP_FROM", "+14155238886"),
                         ("TWILIO_VOICE_FROM", "4155550100")):
            with self.subTest(bad=var), self.assertRaises(SystemExit):
                put_secrets.read_credentials({**GOOD, var: bad})

    def test_secret_is_created_then_updated_with_the_json_the_lambdas_read(self):
        client = boto3.client("secretsmanager", region_name=REGION)
        creds = put_secrets.read_credentials(GOOD)
        self.assertEqual(put_secrets.upsert_secret(client, "ecolink/twilio", creds), "created")
        creds["auth_token"] = "rotated"
        self.assertEqual(put_secrets.upsert_secret(client, "ecolink/twilio", creds), "updated")
        stored = json.loads(client.get_secret_value(SecretId="ecolink/twilio")["SecretString"])
        self.assertEqual(stored, {"account_sid": GOOD["TWILIO_ACCOUNT_SID"], "auth_token": "rotated",
                                  "whatsapp_from": "whatsapp:+14155238886", "voice_from": "+14155550100"})

    def test_stored_secret_is_what_the_twilio_adapter_expects(self):
        """Round trip: put_secrets writes it, the adapter's credential loader reads it."""
        put_secrets.upsert_secret(boto3.client("secretsmanager", region_name=REGION), "ecolink/twilio",
                                  put_secrets.read_credentials(GOOD))
        from ecolink.core import secrets
        from ecolink.providers import twilio

        secrets._cache.clear()
        with mock.patch.dict(os.environ, {"TWILIO_SECRET_NAME": "ecolink/twilio"}):
            for var in GOOD:
                os.environ.pop(var, None)
            self.assertEqual(twilio._credentials()["account_sid"], GOOD["TWILIO_ACCOUNT_SID"])


if __name__ == "__main__":
    unittest.main()
