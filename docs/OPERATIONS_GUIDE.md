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

Because both applications use modern vanilla web standards (HTML5, CSS3, ES modules, Leaflet), **no build step, bundler, or `npm install` is required**.

---

### Option A: Recommended — Single Unified Server (Serves Both Apps)

From the project root directory (`Radhi_Team_OffBeat`):

```bash
# Start a local HTTP server on port 8000
python3 -m http.server 8000 --directory frontend
```

Once running, navigate directly to:
- **Public SaaS Landing Page**: [`http://localhost:8000/site/index.html`](http://localhost:8000/site/index.html)
- **Operations & Dispatch Console**: [`http://localhost:8000/dashboard/index.html`](http://localhost:8000/dashboard/index.html)

> **Seamless Navigation**: The landing page includes an **"Ops Console"** button in the floating navbar and footer that navigates straight to `/dashboard/index.html`. The dashboard login screen includes a **"← Back to EcoLink"** link to return to the landing page.

---

### Option B: Serving Individual Folders on Dedicated Ports

If you prefer to serve the landing page and dashboard on separate ports:

```bash
# Terminal 1 — Serve the Public Landing Page on port 8080:
python3 -m http.server 8080 --directory frontend/site

# Terminal 2 — Serve the Operations Console on port 8000:
python3 -m http.server 8000 --directory frontend/dashboard
```

- Landing Page URL: [`http://localhost:8080`](http://localhost:8080)
- Operations Console URL: [`http://localhost:8000`](http://localhost:8000)

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
   - Recycler pins (green circles), drop-off hubs (purple circles), and household pickups (blue circles).
   - Click **"Fit All Markers"** to automatically zoom and bound all active locations.
   - Click **"Center Hub"** to re-center Bangalore Central coordinates.
5. **Demo Reset**: Click the Demo Reset button in the sidebar footer to restore initial test pickups anytime.

---

### 3.3 Connecting the Console to the Deployed Backend

The console ships in demo mode. To use the live backend, edit `frontend/dashboard/config.js`:

```js
window.ECOLINK_CONFIG = { mockMode: false, baseUrl: 'https://<api-id>.execute-api.<region>.amazonaws.com/prod' };
```

`baseUrl` is the `ApiUrl` output of `cdk deploy`. Sign in with a Cognito user (created with the commands
in `backend/README.md`, "Create a dashboard user"). Notes for live mode:

- Viewers see household addresses, phone numbers and map positions only after a pickup is confirmed;
  admins always see them and can override a pickup's status.
- The **Demo Reset** button is disabled against the live backend. Run `python scripts/reset.py --yes` instead.

The stack does not create hosting for `site/` and `dashboard/`. To publish them, put the folders on any static
host (for example an S3 bucket behind CloudFront that you create separately) and set the stack's
`cors_origin` (`CORS_ORIGIN` in `.env`) to that site's origin.
