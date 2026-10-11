# EcoLink Operations & Deployment Manual

This guide covers deployment, running the services, operating the dashboard, and executing automated test suites.

---

## 1. Prerequisites

- Python 3.12+ (or 3.14 with standard libraries)
- AWS CLI configured with appropriate credentials
- Node.js & AWS CDK CLI (if deploying infrastructure via CDK)

---

## 2. Infrastructure Deployment (AWS CDK)

The entire backend and agent stack is defined in `infra/ecolink_stack.py`. The full, step-by-step
procedure (credentials, Twilio webhook, seeding, smoke test) is in **[`backend/README.md`](../backend/README.md)**.
In short:

```bash
cp .env.example .env                 # fill in AWS + Twilio values (git-ignored)
pip install -r infra/requirements.txt
python scripts/put_secrets.py        # Twilio credentials -> AWS Secrets Manager
cd infra && cdk bootstrap && cdk deploy
cd .. && python scripts/seed.py && python scripts/smoke_test.py
```

Settings (Bedrock model, CORS origin, notification email, Twilio secret name) come from `.env`
or `cdk deploy -c key=value`; the stack declares no CloudFormation parameters.

### Deployed Resources:
- **API Gateway**: REST API with an API-key (Lambda) authorizer and a Cognito authorizer.
- **Cognito User Pool**: For dashboard authentication (`admins` and `viewers`).
- **DynamoDB Tables**: `pickups`, `recyclers`, `users`, `rate_table`, and `conversation_state`.
- **S3 Bucket**: Private photo bucket with 7-day automatic lifecycle expiration.
- **SNS Topic**: Pickup status change broadcast topic.
- **AWS Lambda Functions**: Agent function, authorizer, proxy/classify, pickups, pricing, match, voice, webhook, login and dashboard.
- **Not included**: hosting for `site/` and `dashboard/` (they are static files; serve them from any static host).

---

## 3. Running the Frontend (Landing Page & Operations Console)

The EcoLink frontend consists of two cohesive, zero-build web applications:
1. **Public Marketing & Product Landing Page** (`site/`): Editorial SaaS showcase with scroll animations, IVR call simulation, and architecture journey.
2. **Operations & Dispatch Console** (`dashboard/`): Cognito-ready control room featuring real-time status counters, live search, interactive Leaflet dispatch map, and master-detail drawer.

Because both applications use modern vanilla web standards (HTML5, CSS3, ES modules, MapLibre GL JS), **no build step, bundler, or `npm install` is required**.

---

### Option A: Recommended — Dashboard dev server (injects Amazon Location key)

The dashboard uses **Amazon Location Service** tiles (MapLibre GL JS). The API key is read from `.env`
and injected at serve time — it is never committed to git.

```bash
# 1. Add your Amazon Location API key to .env:
#    AMAZON_LOCATION_API_KEY=v1.public.eyJ...

# 2. Start the dev server from the project root:
python scripts/serve_dashboard.py
```

Once running, navigate to:
- **Operations & Dispatch Console**: [`http://localhost:8000/dashboard/index.html`](http://localhost:8000/dashboard/index.html)
- **Public SaaS Landing Page**: [`http://localhost:8000/site/index.html`](http://localhost:8000/site/index.html)

> **Seamless Navigation**: The landing page links to the Ops Console; the console header links back to "← Back to EcoLink". These relative links work when both are served from the same server.

---

### Option B: Plain static server (no Amazon Location map tiles)

If you don't need the live map, any static server works:

```bash
python3 -m http.server 8000 --directory frontend
```

The map tab will show a blank canvas until an API key is configured. All other tabs (Pickups, WhatsApp Users) work normally.

---

### Option C: Using Node.js or Alternative Local Servers

If you work primarily with Node.js:

```bash
# Using npx serve (Project root):
npx serve -l 8000 .

# Or using live-server for auto-reload on file edits:
npx live-server --port=8000
```

---

### 3.1 Operations Console Authentication & Roles

When accessing `dashboard/index.html`, you are greeted by the Cognito-ready authentication gateway:

| Persona | Email | Password | Permissions & Privacy Behavior |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@ecolink.internal` | *(Any value in mock mode)* | Full visibility. Sees raw customer phone numbers, complete household addresses at all times, and force-override actions. |
| **Viewer / Operator** | `ops@ecolink.internal` | *(Any value in mock mode)* | Address Privacy Rule enforced. Household address remains masked (`[Hidden until confirmed]`) until a recycler accepts the offer. |

---

### 3.2 Interactive Features to Verify in the Dashboard

1. **Live Search**: Type into the search input (`#pickup-search`) to filter pickups instantly across ID, item name, household phone, recycler name, or status.
2. **Progression Filters**: Click any filter chip (`All`, `Requested`, `Offered`, `Confirmed`, `Completed`, `Declined`) — watch the live count badges update automatically.
3. **Master-Detail Drawer**: Click **"View Details →"** on any row to open the drawer:
   - Visual 4-stage lifecycle stepper (`Requested` $\rightarrow$ `Offered` $\rightarrow$ `Confirmed` $\rightarrow$ `Completed`).
   - Hazard warning flags on battery / e-waste items.
   - Address privacy card (green unlocked badge vs amber security shield).
   - IVR Outbound Calling banner for pickups in `offered` state.
   - Agent action log timeline and manual override transition buttons.
4. **Live Dispatch Map**: Click the **"Live Dispatch"** tab in the sidebar:
   - Powered by **MapLibre GL JS + Amazon Location Service** tiles (replaces OpenStreetMap/Leaflet).
   - Recycler pins (green), authorised drop-off hubs (purple), household pickups (blue).
   - **"You are here"** pulsing blue marker via browser geolocation.
   - Click **"Fit All Markers"** to zoom-fit all active locations.
   - Click **"Center Hub"** to fly to your GPS position (falls back to Bangalore Central).
5. **Demo Reset**: Click the Demo Reset button in the sidebar footer to restore initial test pickups anytime.

---

### 3.3 Connecting the Console to the Deployed Backend

`frontend/dashboard/config.js` is **git-ignored** — never committed. Each developer maintains their own copy.

```bash
cp frontend/dashboard/config.example.js frontend/dashboard/config.js
# then fill in the values below
```

| Field | Where to get it |
|---|---|
| `baseUrl` | `ApiUrl` output of `cdk deploy` |
| `cognito.userPoolId` | `UserPoolId` output of `cdk deploy` |
| `cognito.clientId` | `UserPoolClientId` output of `cdk deploy` |
| `amazonLocation.apiKey` | Add as `AMAZON_LOCATION_API_KEY` in `.env`; injected automatically by `scripts/serve_dashboard.py` |

Set `mockMode: false`, then run `python scripts/serve_dashboard.py` and sign in with a Cognito user
(see `backend/README.md`, "Create a dashboard user").

Notes for live mode:
- Viewers see addresses and phone numbers only after a pickup is confirmed; admins always see them.
- The **Demo Reset** button is disabled against the live backend. Run `python scripts/reset.py --yes` instead.

The stack does not create hosting for `site/` and `dashboard/`. To publish them statically (e.g. S3 + CloudFront),
set `CORS_ORIGIN` in `.env` to that origin before `cdk deploy`.
