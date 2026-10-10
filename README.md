# EcoLink System & Operations

EcoLink is a WhatsApp-first assistant for household waste management built with the Strands Agents SDK and AWS serverless architecture (Lambda, API Gateway, DynamoDB, Bedrock, S3, SNS, Cognito).

---

## Architecture Overview

```
EcoLink/
├── agent/                       # WhatsApp Strands Agent (Bedrock Nova Pro)
│   ├── agent_core.py            # Guardrails (Confirmation gate, Hazardous filter, Price regex)
│   ├── tools.py                 # 7 tools (classify, price, quote, recyclers, pickups, voice)
│   ├── state.py                 # DynamoDB state machine with optimistic locking
│   ├── prompt.py                # System prompt constant (English & Hindi)
│   └── lambda_handler.py        # AWS Lambda handler
├── backend/                     # Backend API & Integrations
│   └── ecolink/
│       ├── handlers/            # Pickups, pricing, match, voice, proxy, webhooks
│       ├── core/                # Core business logic & address security rules
│       └── providers/           # Swappable Messaging & Voice (Twilio / AWS)
├── conversation/                # WhatsApp Flow & Content Design
│   ├── flow.json                # State-machine spec with guards, actions & edge cases
│   └── templates.json           # Bilingual templates (<300 chars, ESTIMATE tags, voice script)
├── dashboard/                   # Operations Dashboard (Vanilla JS Web App)
│   ├── index.html               # Cognito-ready layout, stats, pickup list, map & drawer
│   ├── style.css                # Polished UI suitable for demo screen recording
│   ├── api.js                   # Mock/Cognito API client with Address Privacy rules
│   └── app.js                   # Master-detail controller & Leaflet/Amazon Location map
├── infra/                       # AWS CDK Infrastructure Stack
│   ├── app.py
│   └── ecolink_stack.py
├── scripts/                     # Seed and reset utilities
│   ├── seed.py
│   ├── reset.py
│   └── demo_data.py
├── site/                        # Premium SaaS Landing Page
│   ├── index.html               # Product storytelling, typography, and visual sections
│   ├── styles.css               # Design system, glassmorphism, responsive styles
│   └── app.js                   # Smooth scroll parallax, flow progress & phone call animation
├── tests/                       # Comprehensive test suites
│   ├── test_conversation_design.py  # Verifies templates, character limits & safety rules
│   ├── test_address_rule.py          # Verifies household address privacy
│   ├── test_backend_api.py           # Backend endpoints contract
│   ├── test_matching.py              # Recycler matching & geocoding
│   └── test_scenarios.py             # Agent conversation flow scenarios
└── README.md
```

---

## Backend & AWS deployment

The API, agent, infrastructure (AWS CDK), seed/reset scripts and deploy steps are documented in
**[`backend/README.md`](backend/README.md)**; the agent itself in [`agent/README.md`](agent/README.md).
Copy `.env.example` to `.env`, fill in your AWS and Twilio values, then `python scripts/put_secrets.py`,
`cd infra && cdk deploy`, `python scripts/seed.py` and `python scripts/smoke_test.py`.

---

## 🚀 Quickstart: Running the Frontend

The EcoLink frontend is zero-build (Vanilla HTML5, CSS3, ES modules, Leaflet) and requires no build pipeline, bundler, or `npm install`.

### 1. Recommended Method (Single Unified Server)

Serve the repository root on port `8000`:

```bash
python3 -m http.server 8000
```

Once running, visit:
- **Public SaaS Landing Page**: [`http://localhost:8000/site/index.html`](http://localhost:8000/site/index.html)
- **Operations & Dispatch Console**: [`http://localhost:8000/dashboard/index.html`](http://localhost:8000/dashboard/index.html)

> **Seamless Navigation**: The Landing Page navbar and footer contain direct links to **"Ops Console"** (`/dashboard/index.html`). The Ops Console header provides a **"← Back to EcoLink"** link.

### 2. Alternative Method (Dedicated Ports)

If you prefer to serve each folder independently:

```bash
# Terminal 1: Public Landing Page on port 8080
python3 -m http.server 8080 --directory site

# Terminal 2: Operations Console on port 8000
python3 -m http.server 8000 --directory dashboard
```

- Landing Page: [`http://localhost:8080`](http://localhost:8080)
- Operations Console: [`http://localhost:8000`](http://localhost:8000)

### 3. Using Node.js or Alternative Local Servers

```bash
# With npx serve:
npx serve -l 8000 .

# Or with live-server for live reload:
npx live-server --port=8000
```

### 4. Operations Console Demo Login Credentials

The dashboard includes a Cognito-ready authentication gateway with mock mode enabled by default:

| Role | Email | Password | Privacy & Access Behavior |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@ecolink.internal` | *(Any value)* | Full visibility. Sees customer phone numbers, complete household addresses at all times, and manual status override buttons. |
| **Viewer / Operator** | `ops@ecolink.internal` | *(Any value)* | Address Privacy Enforced. Household addresses remain masked (`[Hidden until confirmed]`) until a recycler accepts the offer. |

---

## Part A: Conversation Design

- **State Machine (`conversation/flow.json`)**:
  - Regular waste and E-waste branches with language negotiation (English/Hindi).
  - Explicit confirmation check: pickups cannot be created until the user explicitly says YES / हाँ.
  - Automated routing for hazardous waste (batteries & hazardous e-waste are routed to authorised drop-off points, never regular pickup).
  - Detailed edge-case specifications: blurry photos, unknown items, user cancellation, silence nudges, and recycler decline/no-answer handling.
- **Bilingual Templates (`conversation/templates.json`)**:
  - All messages kept strictly under 300 characters for high readability on mobile devices.
  - Interactive quick-reply buttons and WhatsApp list formats.
  - Unmissable **`🏷️ ESTIMATE`** tag applied wherever prices are quoted.
  - Phone offer voice script for recyclers under 25 seconds (DTMF Key 1: Accept, Key 2: Decline), with zero leakage of household addresses prior to acceptance.

---

## Part B: Operations Dashboard

The dashboard (`dashboard/`) is a lightweight, responsive JavaScript web application requiring no build step, making it immediate to run locally or host statically on S3 + CloudFront.

### Features
1. **Cognito-Ready Login**: Supports user login with permission group tags (`admins` seeing raw address information, `viewers` enforcing privacy rules where addresses remain hidden until pickup confirmation).
2. **Pickup Fleet Management**: Real-time pickup list with status chips (`requested`, `offered`, `confirmed`, `completed`, `declined`), search/status filters, and summary metric cards.
3. **Master-Detail Drawer**: Inspect individual orders, item breakdown with estimated price range, and the agent's step-by-step action log.
4. **Live Dispatch Map**: Interactive map displaying households requesting pickup alongside available kabadiwalas and authorised drop-off facilities. Powered by OpenStreetMap / Amazon Location Service.
5. **Demo Mode Reset Button**: Allows instant restoration of default mock data during demo recordings or presentations.

### Running the Dashboard Locally

```bash
# Serve the dashboard folder using any HTTP server:
python -m http.server 8000 --directory dashboard
# Open http://localhost:8000 in your browser
```

Credentials for demo login:
- **Email**: `admin@ecolink.internal` (Admin access) or `ops@ecolink.internal` (Viewer access)
- **Password**: Any password (demo mock enabled by default)

---

## Part C: Premium SaaS Landing Page

The marketing and product landing page (`site/`) is an editorial, award-worthy SaaS website showcasing the entire waste-dispatch pipeline:

```bash
# Serve the landing page locally:
python -m http.server 8080 --directory site
# Open http://localhost:8080 in your browser
```

Features:
- **Oversized Hero Composition**: Merged floating interfaces displaying WhatsApp chat, Bedrock vision extraction, and geospatial partner matching.
- **Scroll-Linked Visual Journey**: A continuous 5-step path connecting user message to final confirmed pickup.
- **Cinematic IVR Phone Call Simulation**: Visualises automated recycler phone dispatch with single DTMF keypad response.
- **Monochromatic Privacy Gate**: Highlights 4-step address protection workflow and zero-leakage security rules.
- **Interactive Infrastructure Pipeline**: Animated particles demonstrating the serverless AWS architecture.

---

## Running the Automated Tests

```bash
# Install test requirements
pip install -r requirements.txt -r requirements-dev.txt

# Run all test suites
pytest
```
