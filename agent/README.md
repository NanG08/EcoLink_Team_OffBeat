# EcoLink agent (Strands Agents SDK)

WhatsApp-first waste-management assistant built with the Strands Agents SDK on AWS.

---

## Project structure

```
Raadhi_Team_OffBeat/
├── agent/
│   ├── __init__.py
│   ├── api_client.py      # Thin HTTP client – inject a mock for tests
│   ├── tools.py           # 7 Strands tool functions with structured logging
│   ├── state.py           # DynamoDB conversation-state manager
│   ├── prompt.py          # System prompt constant
│   ├── agent_core.py      # EcoLinkAgent class + Python guardrails
│   └── lambda_handler.py  # AWS Lambda entry point (handler)
├── tests/
│   ├── __init__.py
│   └── test_scenarios.py  # 10 scenario tests with mocked tools
├── requirements.txt
└── README.md  (this file lives in agent/)
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `EcoLink_API_BASE_URL` | Yes | – | Backend API base URL (no trailing slash) |
| `EcoLink_API_KEY` | Yes | – | Bearer token for the backend API |
| `EcoLink_REQUEST_TIMEOUT` | No | `10` | HTTP timeout in seconds |
| `DYNAMODB_STATE_TABLE` | No | `EcoLink-conversation-state` | DynamoDB table name |
| `AWS_REGION` | No | `ap-south-1` | AWS region for DynamoDB |
| `STRANDS_MODEL_ID` | No | `us.amazon.nova-pro-v1:0` | Bedrock model ID for the agent |

No values are hard-coded. Set all secrets in Lambda environment variables or AWS Secrets Manager.

---

## Local setup

```bash
# 1. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set required env vars
$env:EcoLink_API_BASE_URL = "https://api.EcoLink.example.com"
$env:EcoLink_API_KEY       = "your-api-key-here"
# (Linux/macOS: export EcoLink_API_BASE_URL=... )
```

---

## Running tests

The test suite stubs out `strands`, `boto3`, and `botocore` so no AWS credentials or real API are needed.

```bash
# Option A – run directly
python tests/test_scenarios.py

# Option B – run via pytest
python -m pytest tests/test_scenarios.py -v
```

Expected output:

```
PASS  Scenario 1: Happy path
PASS  Scenario 2: Wrong classification corrected by user
PASS  Scenario 3: Battery detected → safe drop-off enforced
PASS  Scenario 4: Pickup blocked without explicit confirmation
PASS  Scenario 5: Decline then reschedule
PASS  Scenario 6: Hindi conversation flow (tip check)
PASS  Scenario 7: Tool failure returns error dict (no raw exception)
PASS  Scenario 8: E-waste drop-off path
PASS  Scenario 9: All price outputs use 'estimated Rs X-Y'
PASS  Scenario 10: update_pickup valid/invalid statuses
══════════════════════════════════════════════════
  Total : 10
  Passed: 10
  Failed: 0
══════════════════════════════════════════════════
```

---

## Deploying

The agent Lambda (bundled with `strands-agents` and `requests` from `agent/requirements-lambda.txt`), its
DynamoDB state table, IAM role and environment variables are all created by the CDK stack in `infra/`.
See [`backend/README.md`](../backend/README.md) for the deploy steps. Inside Lambda the agent reads its API key
from Secrets Manager (`EcoLink_API_KEY_SECRET_NAME`), so no key sits in the function configuration.

Location: the messaging webhook prefixes each message with `[USER_LOCATION: lat=X lng=Y]` when it knows the
user's WhatsApp location; the system prompt tells the agent to use exactly those coordinates for `find_recyclers`.

---

## API contract summary

| Endpoint | Tool |
|---|---|
| `POST /classify` | `classify_image` |
| `GET /price` | `get_price` |
| `POST /ewaste/quote` | `ewaste_quote` |
| `GET /recyclers/match` | `find_recyclers` |
| `POST /pickups` | `create_pickup` |
| `PATCH /pickups/{id}` | `update_pickup` |
| `POST /voice/offer` | `offer_to_recycler` |

---

## Guardrails (enforced in Python, not just in the prompt)

1. **Confirmation gate** – `create_pickup` checks `confirmed=True`. The agent sets this flag only after detecting an explicit "yes" / "हाँ" from the user via regex.
2. **Hazardous block** – `create_pickup` rejects any item whose label is `battery` or `ewaste` with a descriptive error. The agent also intercepts pickup-intent messages for hazardous items before calling the LLM.
3. **Price formatting** – `get_price` and `ewaste_quote` both return a `formatted` field (`estimated Rs X-Y`). A post-processing regex in `agent_core.py` normalises any raw price the LLM might emit.

---

## Assumptions

1. **Strands Agents SDK version** – Using `strands-agents 0.1.9`. The `@tool` decorator and `Agent` class signatures match that version. Update `requirements.txt` if the SDK changes.
2. **Model** – Defaults to `us.amazon.nova-pro-v1:0` (Amazon Nova Pro via Bedrock). Change `STRANDS_MODEL_ID` to use any Bedrock-compatible model (e.g. `anthropic.claude-3-5-sonnet-20241022-v2:0`).
3. **Image URLs** – Images are passed as pre-signed S3 URLs. The agent does not upload images; that is handled upstream by the WhatsApp webhook layer.
4. **User location** – `lat`/`lng` for `find_recyclers` must be supplied by the caller (resolved from the user's registered address or a WhatsApp location message). The agent passes through whatever the state holds.
5. **Weight estimate** – When calling `get_price`, the agent uses a default weight of `1.0 kg` if the user has not specified a weight. A follow-up question about weight is optional UX.
6. **Hindi detection** – The heuristic checks if >10 % of characters are Devanagari. Romanised Hindi ("haan bhai") is treated as English for language detection; the prompt instructs the model to mirror the user's style regardless.
7. **Optimistic locking** – DynamoDB state uses a `version` counter with a conditional `put_item`. Concurrent Lambda invocations for the same user will cause a `ConditionalCheckFailedException`; the caller (Lambda handler) should retry once. This covers the typical WhatsApp duplicate-message scenario.
8. **Voice offer callback** – `POST /voice/response` (recycler presses 1/2) is handled by a separate Lambda function not included here. That function calls `update_pickup` to set status to `confirmed` or `declined`.
9. **Address privacy** – The agent never passes `address_text` to `find_recyclers` or `offer_to_recycler`. The address is stored only in the DynamoDB state and in the pickup record on the backend; the recycler receives it only after accepting via the voice callback.
10. **No streaming** – The Lambda handler returns a complete JSON response. WhatsApp delivery is handled by the messaging layer that calls this endpoint.
