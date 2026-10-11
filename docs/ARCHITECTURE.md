# EcoLink System Architecture

EcoLink is a WhatsApp-first assistant for household waste management and automated kabadiwala (recycler) dispatch, built on AWS Serverless infrastructure and the Strands Agents SDK.

---

## 1. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph Users & Partners
        H["Household User<br/>(WhatsApp EN/HI)"]
        V["Voice Caller<br/>(Phone Call EN/HI)"]
        R["Recycler / Kabadiwala<br/>(Phone Call & WhatsApp)"]
        OP["Ops Team & Admins<br/>(Dashboard Web App)"]
    end

    subgraph Messaging & Telephony
        TW["Provider Interface<br/>(Twilio / Amazon Connect / SNS)"]
    end

    subgraph Ingress & Security
        APIGW["Amazon API Gateway (REST API)"]
        AUTH_KEY["API Key Authorizer (Lambda)"]
        AUTH_COG["Cognito User Pool Authorizer"]
        COGNITO["Amazon Cognito<br/>(admins & viewers groups)"]
    end

    subgraph Compute & Logic [AWS Lambda]
        WEBHOOK_FN["Messaging Webhook Fn"]
        VOICE_AGENT_FN["Voice Agent Fn<br/>(Twilio STT & Polly TTS)"]
        AGENT_FN["Strands Agent Fn<br/>(Nova Pro / Claude)"]
        PICKUPS_FN["Pickups Fn"]
        MATCH_FN["Recyclers Match Fn"]
        PRICING_FN["Pricing Fn"]
        VOICE_FN["Voice Offer Fn"]
        UPLOADS_FN["Uploads Presign Fn"]
        PROXY_FN["Proxy & Vision Fn"]
    end

    subgraph AI & Speech
        BEDROCK["Amazon Bedrock<br/>(Multimodal Vision & Reasoning)"]
        POLLY["Amazon Polly<br/>(Neural TTS: Raveena / Aditi)"]
    end

    subgraph Storage & State
        S3["Amazon S3<br/>(7-day lifecycle image bucket)"]
        DDB_STATE["DynamoDB: Conversation State<br/>(Optimistic Locking)"]
        DDB_PICKUPS["DynamoDB: Pickups Table"]
        DDB_RECYCLERS["DynamoDB: Recyclers Table"]
        DDB_RATES["DynamoDB: Rate Table"]
        DDB_USERS["DynamoDB: Users Table"]
        SNS["Amazon SNS<br/>(Status Updates Topic)"]
    end

    %% Interactions
    H <-->|"Inbound photos / text / buttons"| TW
    V <-->|"Inbound phone calls (Speech & Polly TTS)"| TW
    TW <-->|"POST /webhooks/messaging"| APIGW
    TW <-->|"POST /voice/agent"| APIGW
    R <-->|"Outbound phone call (DTMF 1/2)<br/>WhatsApp details"| TW
    OP <-->|"Cognito JWT Auth"| APIGW

    APIGW -->|"Validate Token"| AUTH_KEY
    APIGW -->|"Validate JWT"| AUTH_COG
    AUTH_COG --- COGNITO

    APIGW -->|"/webhooks/messaging"| WEBHOOK_FN
    APIGW -->|"/voice/agent, /voice/agent/end"| VOICE_AGENT_FN
    APIGW -->|"/agent/message, /classify"| PROXY_FN
    APIGW -->|"/pickups"| PICKUPS_FN
    APIGW -->|"/recyclers/match"| MATCH_FN
    APIGW -->|"/price, /ewaste/quote"| PRICING_FN
    APIGW -->|"/voice/offer, /voice/response"| VOICE_FN
    APIGW -->|"/uploads/presign"| UPLOADS_FN

    WEBHOOK_FN --> AGENT_FN
    VOICE_AGENT_FN --> AGENT_FN
    VOICE_AGENT_FN <--> DDB_STATE
    VOICE_AGENT_FN <--> DDB_USERS
    VOICE_AGENT_FN -.->|"TwiML Polly speech"| POLLY
    PROXY_FN --> AGENT_FN
    PROXY_FN --> BEDROCK
    AGENT_FN --> BEDROCK
    AGENT_FN <--> DDB_STATE
    AGENT_FN -->|"Calls backend API"| APIGW

    PICKUPS_FN <--> DDB_PICKUPS
    MATCH_FN <--> DDB_RECYCLERS
    PRICING_FN <--> DDB_RATES
    WEBHOOK_FN <--> DDB_USERS
    UPLOADS_FN --> S3

    VOICE_FN --> TW
    VOICE_FN <--> DDB_PICKUPS
    VOICE_FN --> SNS
    PICKUPS_FN --> SNS
```

---

## 2. Core Subsystems

### 2.1 The Agent Subsystem (`agent/`)
- **Engine**: Strands Agents SDK running on AWS Bedrock (`us.amazon.nova-pro-v1:0` by default).
- **Core Guardrails**: Enforced programmatically in Python (`agent/agent_core.py`):
  1. **Confirmation Gate**: `create_pickup` requires `confirmed=True`, set only on explicit affirmative regex (`yes`, `haan`, `हाँ`, `theek hai`).
  2. **Hazardous Block**: Hazardous labels (`battery`, `ewaste`) cannot be scheduled for standard pickup. Diverted directly to safe drop-off centres.
  3. **Price Normalisation**: All price estimations are strictly normalised to `estimated Rs X-Y`.
- **State Management**: DynamoDB conversation store with versioned optimistic concurrency control (`agent/state.py`).

### 2.2 Backend & API Services (`backend/ecolink/`)
- **Handlers**:
  - `pickups`: Manages lifecycle transitions (`requested` $\rightarrow$ `offered` $\rightarrow$ `confirmed` $\rightarrow$ `completed`/`declined`/`cancelled`).
  - `pricing`: Rate calculation for regular scrap (Rs/kg) and depreciated resale valuation for e-waste.
  - `recyclers_match`: Haversine geospatial proximity query matching waste categories against recycler capabilities.
  - `voice`: Initiates automated IVR calls to recyclers and receives DTMF response callbacks (`1` to accept, `2` to decline).
  - `voice_agent`: Handles inbound conversational phone calls from households (Speech-to-Text via Twilio, reasoning via Strands agent, bilingual Text-to-Speech via Amazon Polly neural voices).
  - `messaging_webhook`: Ingests WhatsApp media and user messages, invoking the agent.
- **Provider Interface**: Pluggable provider interface (`backend/ecolink/providers/base.py`) allowing zero-code switching between Twilio sandbox and native AWS SMS/Connect.

### 2.3 Operations Dashboard & Dispatch Console (`dashboard/`)
- **Authentication**: AWS Cognito User Pool with role-based group authorization (`admins` vs `viewers`).
- **Address Protection**: Non-admin dashboard viewers are prevented from accessing household addresses until a pickup is confirmed.
- **Map**: MapLibre GL JS + Amazon Location Service tiles. Geolocation "You are here" marker. API key loaded from `.env` via `scripts/serve_dashboard.py` — never committed to git.
- **Tabs**: Pickups fleet view, Live Dispatch map, WhatsApp Users (joined from `EcoLink-users` + `EcoLink-conversation-state`).
- **Log Streaming & Telemetry**: Detailed audit trail of the Bedrock agent's decision-making and tool invocations, coupled with an interactive 4-stage lifecycle stepper.
- **Real-Time Client**: Zero-build vanilla architecture with client-side API abstraction (`api.js`) switching between mock demo state and live API Gateway endpoints.
- **Config safety**: `config.js` is git-ignored; `config.example.js` is the committed template.

### 2.4 Public Landing Page (`site/`)
- **Product Storytelling**: Editorial SaaS interface highlighting the end-to-end vision and dispatch journey.
- **Scroll Parallax & Animations**: Continuous 5-step visual flow linking photo classification, valuation, recycler matching, IVR offer, and pickup confirmation.
- **Interactive IVR Simulator**: Demonstrates the automated phone offer to recyclers with live DTMF keypad interaction.
- **Unified Navigation Bridge**: Direct link to the Operations Console (`/dashboard/index.html`) via navbar button and footer link.

### 2.5 Conversational Voice Agent Subsystem (`voice_agent.py` & `voice_agent_twiml.py`)
- **Dual-Channel Telephony Parity**: Provides phone telephony access to the identical Strands agent that powers WhatsApp, enabling hands-free natural voice interaction for non-smartphone or Hindi/English phone callers.
- **Inbound Conversation Loop**:
  1. Caller dials the EcoLink Twilio number $\rightarrow$ Twilio triggers `POST /voice/agent`.
  2. First turn returns a bilingual TwiML greeting with `<Gather input="speech">`.
  3. Caller speaks $\rightarrow$ Twilio transcribes speech and sends `SpeechResult` to `POST /voice/agent`.
  4. `VoiceAgentFn` invokes `EcoLinkAgent.handle(user_id, text=speech_result)`.
  5. The agent reply is spoken via Amazon Polly neural voices with barge-in support (`<Say>` nested inside `<Gather>`).
  6. The loop repeats turn-by-turn until the user hangs up or says goodbye.
- **Identity & State Isolation**: Callers are mapped into DynamoDB `EcoLink-conversation-state` using the `voice:+<E164>` partition key (falling back to `voice:call:<CallSid>`), preventing session collisions with WhatsApp (`whatsapp:+<E164>`) while preserving conversation history across multiple calls from the same phone number.
- **Bilingual Neural TTS**: Automatic script detection routes Devanagari Hindi text to Amazon Polly `Aditi` (`hi-IN`) and English text to `Raveena` (`en-IN`).
- **Safety, Intent & Escape Hatches**:
  - **Identical Guardrails**: The confirmation gate, hazardous item diversion (`battery`, `ewaste`), and price estimation ranges apply strictly over voice.
  - **Goodbye Detection**: Gracefully terminates the call when intents like "bye", "goodbye", "band karo", or "अलविदा" are detected.
  - **Operator Transfer**: If a user asks for a human ("operator", "human", "insaan", "manushya"), the call transfers via `<Dial>` to `VOICE_TRANSFER_NUMBER`.
- **Security & Infrastructure**:
  - Validates `X-Twilio-Signature` HMAC on every turn; invalid requests are rejected with `403`.
  - Implemented in `VoiceAgentFn` (Python 3.12, 512 MB, 55s timeout, ARM64) with status callback logging at `POST /voice/agent/end`.

---

## 3. Data Flow & Sequence Diagram

### 3.1 WhatsApp Pickup & Recycler Dispatch Flow

```mermaid
sequenceDiagram
    autonumber
    actor Household as Household (WhatsApp)
    participant AGW as API Gateway
    participant Agent as Strands Agent
    participant Bedrock as AWS Bedrock
    participant PickupService as Pickup Service
    participant Recycler as Recycler (Phone)
    participant Dashboard as Operations Dashboard

    Household->>AGW: Sends photo of scrap
    AGW->>Agent: Inbound Message Event
    Agent->>Bedrock: Classify image
    Bedrock-->>Agent: Labels: paper, plastic (Non-hazardous)
    Agent-->>Household: "Found Paper & Plastic. Estimate: Rs 110-185. Book pickup?"
    Household->>Agent: "Yes, book for tomorrow 10am"
    Agent->>PickupService: POST /pickups (status: requested)
    PickupService->>Dashboard: Status update (SNS) -> Requested
    Agent->>PickupService: POST /voice/offer
    PickupService->>Recycler: Automated Phone Call ("Press 1 to accept...")
    Note over Recycler,PickupService: Recycler only hears items, weight & distance (NO address)
    Recycler->>PickupService: DTMF 1 (Accept)
    PickupService->>PickupService: Transition status: CONFIRMED
    par Notification to Recycler
        PickupService-->>Recycler: WhatsApp: Full household address sent
    and Notification to Household
        PickupService-->>Household: WhatsApp: Confirmation with Recycler Name
    and Dashboard Update
        PickupService->>Dashboard: Status update (SNS) -> Confirmed
    end
```

### 3.2 Inbound Conversational Voice Agent Flow

```mermaid
sequenceDiagram
    autonumber
    actor Caller as Voice Caller (Phone)
    participant Twilio as Twilio Voice Service
    participant AGW as API Gateway
    participant VoiceAgentFn as VoiceAgentFn (Lambda)
    participant Agent as Strands Agent (Bedrock)
    participant DDB as DynamoDB (conversation_state)

    Caller->>Twilio: Dials EcoLink phone number
    Twilio->>AGW: POST /voice/agent (CallSid, Caller, no SpeechResult)
    AGW->>VoiceAgentFn: Inbound call event
    VoiceAgentFn-->>Twilio: TwiML 200 OK (<Gather> + <Say voice="Polly.Raveena">Greeting...</Say>)
    Twilio-->>Caller: Speaks greeting via Amazon Polly neural voice
    Caller->>Twilio: Speaks: "I have 5 kg newspapers and 2 kg cardboard"
    Twilio->>AGW: POST /voice/agent (SpeechResult, CallSid, Caller)
    AGW->>VoiceAgentFn: Speech turn event
    VoiceAgentFn->>DDB: Fetch conversation state (PK: voice:+E164)
    VoiceAgentFn->>Agent: EcoLinkAgent.handle(user_id, text=SpeechResult)
    Agent-->>VoiceAgentFn: "Paper and cardboard... estimated Rs 45-70..."
    VoiceAgentFn->>DDB: Persist updated state (optimistic lock)
    VoiceAgentFn-->>Twilio: TwiML 200 OK (<Gather> + <Say reply> with barge-in)
    Twilio-->>Caller: Speaks price quote & asks for confirmation
    Note over Caller,Twilio: Repeats turn-by-turn until caller says "bye" or disconnects
```

---

## 4. Key Security & Privacy Guarantees

1. **Address Privacy Rule**: Household address is stored only in DynamoDB. It is strictly never passed to any candidate recycler during matching or voice offers. Address details are dispatched to the recycler **only after** they press `1` to confirm acceptance.
2. **Media Retention Policy**: S3 lifecycle rules automatically delete uploaded waste photos after 7 days.
3. **Least Privilege IAM**: Granular AWS IAM roles restrict Lambda functions to specific DynamoDB table partition keys, S3 prefixes, and Bedrock model ARNs.

---

## 5. Serverless Compute Specifications

| Function Name | Handler | Memory | Timeout | Key Permissions & Integrations |
| :--- | :--- | :--- | :--- | :--- |
| **`MessagingWebhookFn`** | `ecolink.handlers.messaging_webhook.handler` | 256 MB | 60s | DynamoDB `users`, S3 `images` (put), Twilio Secrets, invokes `AgentFn` |
| **`VoiceAgentFn`** | `ecolink.handlers.voice_agent.handler` | 512 MB | 55s | DynamoDB `conversation_state`, `users`, S3 `images` (get), Twilio Secrets, invokes `AgentFn`, Amazon Polly TwiML |
| **`AgentFn`** | `agent.lambda_handler.handler` | 1024 MB | 60s | AWS Bedrock (`Nova Pro`/`Claude`), DynamoDB `conversation_state`, S3 `models`, backend API |
| **`PickupsFn`** | `ecolink.handlers.pickups.handler` | 256 MB | 30s | DynamoDB `pickups`, `rate_table`, SNS status topic |
| **`MatchFn`** | `ecolink.handlers.recyclers_match.handler` | 256 MB | 30s | DynamoDB `recyclers` |
| **`PricingFn`** | `ecolink.handlers.pricing.handler` | 256 MB | 30s | DynamoDB `rate_table` |
| **`VoiceFn`** | `ecolink.handlers.voice.handler` | 256 MB | 30s | DynamoDB `pickups`, Twilio Voice API, SNS status topic |
| **`DashboardFn`** | `ecolink.handlers.dashboard.handler` | 256 MB | 30s | DynamoDB `recyclers`, `users`, `conversation_state`, `pickups` |
| **`UploadsPresignFn`** | `ecolink.handlers.uploads.handler` | 128 MB | 15s | S3 `images` presigned URLs |
| **`ProxyFn`** | `ecolink.handlers.proxy.handler` | 512 MB | 60s | Bedrock vision fallback, invokes `AgentFn` |


