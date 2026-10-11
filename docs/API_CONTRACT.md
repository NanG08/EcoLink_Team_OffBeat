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

---

### 1.9 POST `/voice/agent` & `/voice/agent/end`
Conversational telephony interface for inbound household voice calls powered by Twilio Speech-to-Text and Amazon Polly Neural TTS.

#### 1.9.1 POST `/voice/agent` (Call & Turn Webhook)
Called by Twilio on incoming call connection (first turn) and on each subsequent speech turn.

- **Authentication**: `X-Twilio-Signature` HMAC verification (no API key header).
- **Request Content-Type**: `application/x-www-form-urlencoded`
- **Request Parameters**:
  - `CallSid` (string, required): Twilio unique call identifier (e.g. `CA1234567890abcdef`).
  - `Caller` or `From` (string, optional): Caller's E.164 phone number. Used to construct state ID `voice:+<E164>`.
  - `SpeechResult` (string, optional): Transcribed speech. Empty on initial call connection; contains user's spoken utterance on subsequent turns.
  - `Confidence` (string, optional): Transcription confidence score (`0.0` – `1.0`).
  - `CallStatus` (string, optional): Twilio call status (`ringing`, `in-progress`).
- **Response** (`200 OK`, `Content-Type: text/xml; charset=utf-8`):

  **Turn 1 — Initial Greeting** (no `SpeechResult`):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response>
    <Gather input="speech" action="https://<api-id>.execute-api.ap-south-1.amazonaws.com/prod/voice/agent" method="POST" language="en-IN" speechModel="phone_call" speechTimeout="3" speechMaxSpeech="15">
      <Say voice="Polly.Raveena" language="en-IN">Welcome to EcoLink, your waste recycling assistant. You can tell me what waste you have, and I will help you sell or recycle it. Please speak after the beep.</Say>
    </Gather>
    <Say voice="Polly.Raveena" language="en-IN">I didn't catch that. Please speak after the beep.</Say>
  </Response>
  ```

  **Subsequent Turns — Agent Reply** (with barge-in enabled via nested `<Say>` inside `<Gather>`):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response>
    <Gather input="speech" action="https://<api-id>.execute-api.ap-south-1.amazonaws.com/prod/voice/agent" method="POST" language="en-IN" speechModel="phone_call" speechTimeout="3" speechMaxSpeech="15">
      <Say voice="Polly.Raveena" language="en-IN">Paper and cardboard can be sold. For 5 kg paper and 2 kg cardboard, estimated value is 60 to 95 rupees. Would you like me to book a pickup?</Say>
    </Gather>
    <Say voice="Polly.Raveena" language="en-IN">I didn't catch that. Please speak after the beep.</Say>
  </Response>
  ```

  **Hindi Turns** (auto-detected when speech contains $>10\%$ Devanagari script):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response>
    <Gather input="speech" action="https://<api-id>.execute-api.ap-south-1.amazonaws.com/prod/voice/agent" method="POST" language="hi-IN" speechModel="phone_call" speechTimeout="3" speechMaxSpeech="15">
      <Say voice="Polly.Aditi" language="hi-IN">नमस्ते, EcoLink में आपका स्वागत है। कृपया अपना कचरा बताएं और हम उसे बेचने या रीसायकल करने में मदद करेंगे। बीप के बाद बोलें।</Say>
    </Gather>
    <Say voice="Polly.Aditi" language="hi-IN">मुझे सुनाई नहीं दिया। कृपया बीप के बाद बोलें।</Say>
  </Response>
  ```

  **Goodbye Intent** (user says "bye", "goodbye", "band karo", "अलविदा"):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response>
    <Say voice="Polly.Raveena" language="en-IN">Thank you for calling EcoLink. Goodbye!</Say>
    <Hangup/>
  </Response>
  ```

  **Operator Transfer Intent** (user asks for "operator", "human", "insaan", "manushya"):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response>
    <Say voice="Polly.Raveena" language="en-IN">Connecting you to our team now. Please hold.</Say>
    <Dial>+919876543210</Dial>
  </Response>
  ```

- **Error Responses**:
  - `403 Forbidden`: Returned when `X-Twilio-Signature` validation fails (`<Response><Hangup/></Response>`).
  - `200 OK` (Graceful TwiML error fallback): Returned if the Bedrock agent throws an internal exception:
    ```xml
    <?xml version="1.0" encoding="UTF-8"?>
    <Response>
      <Say voice="Polly.Raveena" language="en-IN">Sorry, something went wrong. Please try again later.</Say>
      <Hangup/>
    </Response>
    ```

#### 1.9.2 POST `/voice/agent/end` (Status Callback)
Called asynchronously by Twilio when the phone call is terminated or hangs up.

- **Request Parameters**: `CallSid`, `Caller`, `CallStatus`, `CallDuration`
- **Response** (`200 OK`, `Content-Type: text/xml; charset=utf-8`):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Response/>
  ```
- **Action**: Emits structured CloudWatch log `voice_agent_call_ended` with call duration telemetry.


