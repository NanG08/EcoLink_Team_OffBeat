#!/usr/bin/env python
"""CDK entry point:  cd infra && cdk deploy

Settings come from `-c key=value`, then the repo's .env file / environment, then defaults:
  prefix (ECOLINK_PREFIX), bedrock_model_id (BEDROCK_MODEL_ID), twilio_secret_name
  (TWILIO_SECRET_NAME), cors_origin (CORS_ORIGIN), notify_email (NOTIFY_EMAIL),
  public_base_url (custom domain only), retain_data (true keeps tables/bucket on destroy),
  secrets_backend (SECRETS_BACKEND: secretsmanager | ssm),
  classify_primary (CLASSIFY_PRIMARY: bedrock | onnx, which classifier is tried first).
"""

import os
import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import envfile  # noqa: E402

envfile.load_env()

from ecolink_stack import EcoLinkStack  # noqa: E402

app = cdk.App()
ctx = app.node.try_get_context


def setting(context_key: str, env_key: str, default=None):
    return ctx(context_key) or os.environ.get(env_key) or default


EcoLinkStack(
    app,
    "EcoLinkBackend",
    prefix=setting("prefix", "ECOLINK_PREFIX", "EcoLink"),
    bedrock_model_id=setting("bedrock_model_id", "BEDROCK_MODEL_ID", "apac.amazon.nova-pro-v1:0"),
    twilio_secret_name=setting("twilio_secret_name", "TWILIO_SECRET_NAME", "ecolink/twilio"),
    cors_origin=setting("cors_origin", "CORS_ORIGIN", "*"),
    notify_email=setting("notify_email", "NOTIFY_EMAIL"),
    public_base_url=ctx("public_base_url"),
    retain_data=str(ctx("retain_data")).lower() == "true",
    secrets_backend=setting("secrets_backend", "SECRETS_BACKEND", "secretsmanager"),
    classify_primary=setting("classify_primary", "CLASSIFY_PRIMARY", "bedrock"),
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or "ap-south-1",
    ),
)
app.synth()
