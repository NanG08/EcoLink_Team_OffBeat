# EcoLink API & Data Contracts

All endpoints use standard JSON payloads and responses. Field names and types are strictly specified.

---

## 1. Endpoints Specification

### 1.1 POST `/classify`
Uses Amazon Bedrock Multimodal Vision to inspect an image and identify waste components.

- **Headers**: `Authorization: Bearer <API_KEY>`
- **Request Body**:
  ```json
  {
    "image_url": "https://s3.ap-south-1.amazonaws.com/ecolink-bucket/images/example.jpg"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "items": [
      {
        "label": "paper",
        "confidence": 0.94,
        "hazardous": false
      },
      {
        "label": "plastic",
        "confidence": 0.88,
        "hazardous": false
      }
    ]
  }
  ```
- **Valid Labels**: `paper`, `cardboard`, `plastic`, `metal`, `glass`, `ewaste`, `battery`.

---

### 1.2 GET `/price`
Calculates indicative scrap value for a regular waste item.

- **Query Parameters**:
  - `label`: Scrap category (`paper`, `cardboard`, `plastic`, `metal`, `glass`).
  - `weight_kg`: Weight in kilograms (float).
- **Response** (`200 OK`):
  ```json
  {
    "low": 45,
    "high": 63,
    "currency": "INR",
    "updated": "2026-10-07"
  }
  ```

---

### 1.3 POST `/ewaste/quote`
Calculates depreciated resale valuation and recycling guidance for electronic items.

- **Request Body**:
  ```json
  {
    "item_type": "mobile",
    "age_years": 3,
    "condition": "working"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "low": 4096,
    "high": 10240,
    "recommendation": "sell"
  }
  ```
  *(Recommendation is `"sell"` if value warrants resale; otherwise `"dropoff"`).*

---

### 1.4 GET `/recyclers/match`
Dispatches spatial proximity lookup using Haversine calculation against active recyclers.

- **Query Parameters**:
  - `lat`: User latitude (float, e.g. `12.9716`)
  - `lng`: User longitude (float, e.g. `77.5946`)
  - `labels`: Comma-separated categories (e.g. `paper,plastic`)
- **Response** (`200 OK`):
  ```json
  [
    {
      "recycler_id": "R001",
      "name": "Sharma Kabadi Works",
      "distance_km": 1.3,
      "accepts": ["paper", "cardboard", "plastic", "metal", "glass"]
    }
  ]
  ```

---

### 1.5 POST `/pickups`
Creates a new pickup reservation request.

- **Request Body**:
  ```json
  {
    "user_id": "+919876543210",
    "items": [
      { "label": "paper", "weight_kg": 4.5 },
      { "label": "plastic", "weight_kg": 2.0 }
    ],
    "address_text": "Flat 4B, Lakeview Apartments, Indiranagar, Bengaluru 560038",
    "slot": "2026-10-09T10:00:00+05:30"
  }
  ```
- **Response** (`201 Created`):
  ```json
  {
    "pickup_id": "pk_3f9a1c2b7d10",
    "status": "requested"
  }
  ```

---

### 1.6 PATCH `/pickups/{id}`
Transitions pickup status.

- **Allowed Statuses**: `requested`, `offered`, `confirmed`, `completed`, `cancelled`, `declined`.
- **Request Body**:
  ```json
  {
    "status": "confirmed"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "pickup_id": "pk_3f9a1c2b7d10",
    "status": "confirmed"
  }
  ```

---

### 1.7 POST `/voice/offer` & `/voice/response`
Automated telephony dispatch to candidate recycler.

- **POST `/voice/offer`**:
  ```json
  {
    "pickup_id": "pk_3f9a1c2b7d10",
    "recycler_id": "R001"
  }
  ```
- **POST `/voice/response`** (Keypad callback):
  ```json
  {
    "pickup_id": "pk_3f9a1c2b7d10",
    "digit": "1"
  }
  ```
  *(Digit `1` transitions to `confirmed`; Digit `2` transitions to `declined`).*

---

### 1.8 POST `/agent/message`
Main conversation webhook interface invoked by messaging layers.

- **Request Body**:
  ```json
  {
    "user_id": "+919876543210",
    "text": "Yes, please book the pickup",
    "image_url": null,
    "lang": "en"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "reply_text": "Booking now... I'm calling a kabadiwala near you.",
    "actions": [
      { "type": "create_pickup", "pickup_id": "pk_3f9a1c2b7d10" }
    ]
  }
  ```

