"""EcoLink backend: DynamoDB, S3, SNS, Cognito, API Gateway, Lambdas (incl. the agent).

IAM is least-privilege per function: each Lambda gets its own role with only the actions
and resources it uses (see the `grant` calls next to each function).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import aws_cdk as cdk
import jsii
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_kms as kms,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as sm,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
AGENT_DIR = REPO_ROOT / "agent"
sys.path.insert(0, str(BACKEND_DIR))
from ecolink import schema  # noqa: E402  single source of truth for table definitions

RUNTIME = _lambda.Runtime.PYTHON_3_12
ARCH = _lambda.Architecture.ARM_64
DYNAMO_TYPES = {"S": ddb.AttributeType.STRING}


@jsii.implements(cdk.ILocalBundling)
class _AgentPipBundling:
    """Bundle the agent without Docker: pip-install Linux/arm64 wheels, copy agent/*.py."""

    def try_bundle(self, output_dir: str, options: cdk.BundlingOptions) -> bool:  # noqa: ARG002
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
             "-r", str(AGENT_DIR / "requirements-lambda.txt"), "--target", output_dir,
             "--platform", "manylinux2014_aarch64", "--python-version", "3.12",
             "--implementation", "cp", "--only-binary=:all:"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            # Returning False makes CDK fall back to bundling inside Docker.
            print(f"pip failed while bundling the agent (trying Docker):\n{result.stdout}\n{result.stderr}", file=sys.stderr)
            return False
        shutil.copytree(AGENT_DIR, Path(output_dir) / "agent",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "requirements-lambda.txt"))
        return True


def _bedrock_resources(model_id: str, region: str, account: str) -> list[str]:
    """ARNs needed to invoke a model or cross-region inference profile (e.g. apac.amazon.nova-pro-v1:0)."""
    foundation = re.sub(r"^(us|eu|apac|global|us-gov)\.", "", model_id)
    resources = [f"arn:aws:bedrock:*::foundation-model/{foundation}"]
    if foundation != model_id:  # an inference profile: also allow the profile itself
        resources.append(f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}")
    return resources


class EcoLinkStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        prefix: str,
        bedrock_model_id: str,
        twilio_secret_name: str,
        cors_origin: str,
        notify_email: str | None,
        public_base_url: str | None,
        retain_data: bool,
        secrets_backend: str = "secretsmanager",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.prefix = prefix
        self.removal = RemovalPolicy.RETAIN if retain_data else RemovalPolicy.DESTROY
        agent_name = f"{prefix}-agent"  # fixed name: lets other functions reference it without a cycle

        # ── DynamoDB: users, recyclers, pickups, rate_table, conversation_state ──
        self.tables = {key: self._table(key, spec) for key, spec in schema.TABLES.items()}

        # ── S3: private image bucket, 7-day expiry ───────────────────────────────
        self.bucket = s3.Bucket(
            self, "ImagesBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            lifecycle_rules=[s3.LifecycleRule(
                id="expire-images-after-7-days", enabled=True,
                expiration=Duration.days(7),
                abort_incomplete_multipart_upload_after=Duration.days(1),
            )],
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.POST],
                              allowed_origins=[cors_origin], allowed_headers=["*"], max_age=3000)],
            removal_policy=self.removal,
            auto_delete_objects=not retain_data,
        )

        # ── SNS: pickup status notifications ────────────────────────────────────
        self.topic = sns.Topic(
            self, "StatusTopic", topic_name=f"{prefix}-pickup-status",
            master_key=kms.Alias.from_alias_name(self, "SnsKey", "alias/aws/sns"),
        )
        if notify_email:
            self.topic.add_subscription(subs.EmailSubscription(notify_email))

        # ── Secrets: API key + Twilio credentials ───────────────────────────────
        if secrets_backend not in ("secretsmanager", "ssm"):
            raise ValueError("secrets_backend must be 'secretsmanager' or 'ssm'")
        self.secrets_backend = secrets_backend
        self.twilio_secret_name = twilio_secret_name
        self.api_key_name = f"{prefix.lower()}/api-key"
        if secrets_backend == "secretsmanager":
            self.api_key_secret = sm.Secret(
                self, "ApiKeySecret", secret_name=self.api_key_name,
                description="Bearer token for the EcoLink API (used by the agent and other API clients)",
                generate_secret_string=sm.SecretStringGenerator(password_length=48, exclude_punctuation=True),
                removal_policy=self.removal,
            )
            # Twilio credentials are created by the operator (scripts/put_secrets.py); nothing secret in the repo.
            self.twilio_secret = sm.Secret.from_secret_name_v2(self, "TwilioSecret", twilio_secret_name)
        # With 'ssm' both values are SecureString parameters written by scripts/put_secrets.py
        # (CloudFormation cannot create encrypted parameters), and are only granted below.

        # ── Cognito: dashboard login ────────────────────────────────────────────
        self.user_pool = cognito.UserPool(
            self, "DashboardUserPool", user_pool_name=f"{prefix}-dashboard",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=False),
            mfa=cognito.Mfa.OPTIONAL, mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=self.removal,
        )
        self.user_pool_client = self.user_pool.add_client(
            "DashboardClient", generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            prevent_user_existence_errors=True,
            id_token_validity=Duration.hours(1), access_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(7),
        )
        for group, description in (
            ("admins", "Full access, including household addresses before confirmation"),
            ("viewers", "Read-only; household address hidden until a pickup is confirmed"),
        ):
            cognito.CfnUserPoolGroup(self, f"Group{group.title()}", user_pool_id=self.user_pool.user_pool_id,
                                     group_name=group, description=description)

        # ── Lambdas ─────────────────────────────────────────────────────────────
        backend_code = _lambda.Code.from_asset(str(BACKEND_DIR), exclude=["**/__pycache__", "*.pyc"])
        common_env = {
            "LOG_LEVEL": "INFO",
            "CORS_ORIGIN": cors_origin,
            "MESSAGING_PROVIDER": "twilio",
            "VOICE_PROVIDER": "twilio",
            "SECRETS_BACKEND": secrets_backend,
            "TWILIO_SECRET_NAME": twilio_secret_name,
            "API_KEY_SECRET_NAME": self.api_key_name,
            "IMAGES_BUCKET": self.bucket.bucket_name,
            "STATUS_TOPIC_ARN": self.topic.topic_arn,
            "CLASSIFY_MODEL_ID": bedrock_model_id,
            "AGENT_FUNCTION_NAME": agent_name,
            **{spec.env_var: t.table_name for (key, spec), t in zip(schema.TABLES.items(), self.tables.values())},
        }
        if public_base_url:
            common_env["PUBLIC_API_BASE_URL"] = public_base_url

        def fn(name: str, handler: str, *, timeout: int = 15, memory: int = 256) -> _lambda.Function:
            return _lambda.Function(
                self, name, runtime=RUNTIME, architecture=ARCH, code=backend_code,
                handler=f"ecolink.handlers.{handler}.handler", timeout=Duration.seconds(timeout),
                memory_size=memory, environment=dict(common_env),
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY),
            )

        self.authorizer_fn = fn("AuthorizerFn", "authorizer", timeout=5)
        self._grant_api_key(self.authorizer_fn)

        self.pickups_fn = fn("PickupsFn", "pickups")
        self.voice_fn = fn("VoiceFn", "voice")
        for f in (self.pickups_fn, self.voice_fn):  # both can move a pickup to confirmed/declined
            self._allow_table(f, "pickups", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query")
            self._allow_table(f, "recyclers", "dynamodb:GetItem")
            self._allow_table(f, "users", "dynamodb:GetItem")
            self.topic.grant_publish(f)
            self._grant_twilio(f)
        # Dashboard reads (GET /pickups) estimate each pickup's value from the rate table.
        self._allow_table(self.pickups_fn, "rate_table", "dynamodb:GetItem")

        # Dashboard: password sign-in (Cognito InitiateAuth is a public API, so no IAM needed)
        self.auth_fn = fn("AuthFn", "auth", timeout=10)
        self.auth_fn.add_environment("COGNITO_CLIENT_ID", self.user_pool_client.user_pool_client_id)
        self.dashboard_fn = fn("DashboardFn", "dashboard")
        self._allow_table(self.dashboard_fn, "recyclers", "dynamodb:Scan")

        self.match_fn = fn("MatchFn", "recyclers_match")
        self._allow_table(self.match_fn, "recyclers", "dynamodb:Scan")

        self.pricing_fn = fn("PricingFn", "pricing")
        self._allow_table(self.pricing_fn, "rate_table", "dynamodb:GetItem")

        self.uploads_fn = fn("UploadsFn", "uploads")
        self._allow_images(self.uploads_fn, put=True)

        # /classify (Bedrock vision) and /agent/message (invokes the agent Lambda)
        self.proxy_fn = fn("ProxyFn", "proxy", timeout=28)
        self.proxy_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=_bedrock_resources(bedrock_model_id, self.region, self.account)))
        self._allow_agent_invoke(self.proxy_fn, agent_name)

        # Inbound messaging webhook: stores photos, calls the agent, replies via Twilio.
        self.webhook_fn = fn("MessagingWebhookFn", "messaging_webhook", timeout=60)
        self._allow_table(self.webhook_fn, "users", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem")
        self._allow_images(self.webhook_fn, put=True)
        self._grant_twilio(self.webhook_fn)
        self._allow_agent_invoke(self.webhook_fn, agent_name)

        # ── The agent (agent/lambda_handler.py), bundled with its pip dependencies ──
        self.agent_fn = _lambda.Function(
            self, "AgentFn", function_name=agent_name, runtime=RUNTIME, architecture=ARCH,
            handler="agent.lambda_handler.handler", timeout=Duration.seconds(60), memory_size=1024,
            code=_lambda.Code.from_asset(
                str(AGENT_DIR), exclude=["__pycache__", "*.pyc"],
                bundling=cdk.BundlingOptions(
                    image=RUNTIME.bundling_image, platform="linux/arm64", local=_AgentPipBundling(),
                    command=["bash", "-c",  # Docker fallback if local bundling is unavailable
                             "pip install -r requirements-lambda.txt -t /asset-output && "
                             "mkdir -p /asset-output/agent && cp *.py /asset-output/agent/"],
                ),
            ),
            log_group=logs.LogGroup(self, "AgentFnLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        self._allow_table(self.agent_fn, "conversation_state", "dynamodb:GetItem", "dynamodb:PutItem")
        self._grant_api_key(self.agent_fn)
        self.agent_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=_bedrock_resources(bedrock_model_id, self.region, self.account)))

        # ── API Gateway ─────────────────────────────────────────────────────────
        cors_origins = apigw.Cors.ALL_ORIGINS if cors_origin == "*" else [cors_origin]
        self.api = apigw.RestApi(
            self, "Api", rest_api_name=f"{prefix}-api", description="EcoLink backend API",
            cloud_watch_role=False,  # do not change the account-wide API Gateway logging role
            deploy_options=apigw.StageOptions(stage_name="prod", throttling_rate_limit=50,
                                              throttling_burst_limit=100, metrics_enabled=True),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=cors_origins, allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"]),
        )
        for rtype in (apigw.ResponseType.DEFAULT_4_XX, apigw.ResponseType.DEFAULT_5_XX):
            self.api.add_gateway_response(
                f"Cors{rtype.response_type}", type=rtype,
                response_headers={"Access-Control-Allow-Origin": f"'{cors_origin}'"})

        api_key_auth = apigw.TokenAuthorizer(
            self, "ApiKeyAuthorizer", handler=self.authorizer_fn,
            identity_source="method.request.header.Authorization",
            results_cache_ttl=Duration.minutes(5))
        dashboard_auth = apigw.CognitoUserPoolsAuthorizer(
            self, "DashboardAuthorizer", cognito_user_pools=[self.user_pool])

        def integ(f: _lambda.Function) -> apigw.LambdaIntegration:
            return apigw.LambdaIntegration(f, proxy=True)

        def route(resource: apigw.IResource, method: str, f: _lambda.Function, auth) -> None:
            resource.add_method(method, integ(f), authorizer=auth)

        root = self.api.root
        route(root.add_resource("classify"), "POST", self.proxy_fn, api_key_auth)
        route(root.add_resource("agent").add_resource("message"), "POST", self.proxy_fn, api_key_auth)
        route(root.add_resource("price"), "GET", self.pricing_fn, api_key_auth)
        route(root.add_resource("ewaste").add_resource("quote"), "POST", self.pricing_fn, api_key_auth)
        route(root.add_resource("recyclers").add_resource("match"), "GET", self.match_fn, api_key_auth)
        route(root.add_resource("uploads").add_resource("presign"), "POST", self.uploads_fn, api_key_auth)

        pickups = root.add_resource("pickups")
        route(pickups, "POST", self.pickups_fn, api_key_auth)  # the agent
        route(pickups, "GET", self.pickups_fn, dashboard_auth)  # dashboard
        pickup = pickups.add_resource("{id}")
        route(pickup, "PATCH", self.pickups_fn, api_key_auth)
        route(pickup, "GET", self.pickups_fn, dashboard_auth)

        # Operations dashboard (Cognito ID token). Separate paths from the API-key routes above
        # because a method can have only one authorizer.
        route(root.add_resource("auth").add_resource("login"), "POST", self.auth_fn, None)
        dashboard = root.add_resource("dashboard")
        route(dashboard.add_resource("recyclers"), "GET", self.dashboard_fn, dashboard_auth)
        route(dashboard.add_resource("pickups").add_resource("{id}"), "PATCH", self.pickups_fn, dashboard_auth)

        voice = root.add_resource("voice")
        route(voice.add_resource("offer"), "POST", self.voice_fn, api_key_auth)
        # Called by Twilio, so no API-key authorizer: the Twilio signature is verified instead.
        route(voice.add_resource("response"), "POST", self.voice_fn, None)
        route(root.add_resource("webhooks").add_resource("messaging"), "POST", self.webhook_fn, None)

        # ── Agent environment (needs the API URL, hence defined after the API) ──
        for key, value in {
            "EcoLink_API_BASE_URL": self.api.url.rstrip("/"),
            "EcoLink_API_KEY_SECRET_NAME": self.api_key_name,
            "SECRETS_BACKEND": secrets_backend,
            "DYNAMODB_STATE_TABLE": self.tables["conversation_state"].table_name,
            "STRANDS_MODEL_ID": bedrock_model_id,
        }.items():
            self.agent_fn.add_environment(key, value)

        # ── Outputs ─────────────────────────────────────────────────────────────
        outputs = {
            "ApiUrl": self.api.url,
            "TwilioMessagingWebhookUrl": f"{self.api.url}webhooks/messaging",
            "UserPoolId": self.user_pool.user_pool_id,
            "UserPoolClientId": self.user_pool_client.user_pool_client_id,
            "ImagesBucketName": self.bucket.bucket_name,
            "StatusTopicArn": self.topic.topic_arn,
            "ApiKeySecretName": self.api_key_name,
            "AgentFunctionName": agent_name,
        }
        for name, value in outputs.items():
            CfnOutput(self, name, value=value)

    # ── helpers ────────────────────────────────────────────────────────────────
    def _table(self, key: str, spec: schema.TableSpec) -> ddb.Table:
        table = ddb.Table(
            self, f"Table-{key}", table_name=schema.table_name(key, self.prefix),
            partition_key=ddb.Attribute(name=spec.pk, type=DYNAMO_TYPES["S"]),
            sort_key=ddb.Attribute(name=spec.sk, type=DYNAMO_TYPES["S"]) if spec.sk else None,
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute=spec.ttl,
            removal_policy=self.removal,
        )
        for gsi in spec.gsis:
            table.add_global_secondary_index(
                index_name=gsi.name,
                partition_key=ddb.Attribute(name=gsi.pk, type=DYNAMO_TYPES["S"]),
                sort_key=ddb.Attribute(name=gsi.sk, type=DYNAMO_TYPES["S"]),
                projection_type=ddb.ProjectionType.ALL,
            )
        return table

    def _allow_table(self, fn: _lambda.Function, key: str, *actions: str) -> None:
        table = self.tables[key]
        resources = [table.table_arn]
        if schema.TABLES[key].gsis:
            resources.append(f"{table.table_arn}/index/*")
        fn.add_to_role_policy(iam.PolicyStatement(actions=list(actions), resources=resources))

    def _grant_secret(self, fn: _lambda.Function, name: str, secret: sm.ISecret | None) -> None:
        if self.secrets_backend == "ssm":
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/{name.lstrip('/')}"]))
        else:
            secret.grant_read(fn)

    def _grant_api_key(self, fn: _lambda.Function) -> None:
        self._grant_secret(fn, self.api_key_name, getattr(self, "api_key_secret", None))

    def _grant_twilio(self, fn: _lambda.Function) -> None:
        self._grant_secret(fn, self.twilio_secret_name, getattr(self, "twilio_secret", None))

    def _allow_images(self, fn: _lambda.Function, *, put: bool) -> None:
        actions = ["s3:GetObject"] + (["s3:PutObject"] if put else [])
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=actions, resources=[self.bucket.arn_for_objects("images/*")]))

    def _allow_agent_invoke(self, fn: _lambda.Function, agent_name: str) -> None:
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:{agent_name}"]))
