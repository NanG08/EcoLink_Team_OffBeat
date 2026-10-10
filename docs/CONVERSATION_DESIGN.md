# EcoLink Conversation & Guardrails Design

This document details the WhatsApp interaction flow, user experience guidelines, safety guardrails, and multilingual conversational logic.

---

## 1. Flow Diagrams

### 1.1 Regular Waste Flow
```mermaid
flowchart TD
    START([Start / Inbound Message]) --> LANG{Language Chosen?}
    LANG -- No --> ASK_LANG[Ask Language: EN / HI]
    LANG -- Yes --> MENU[Choose Path: Regular or E-waste]

    MENU -->|Regular Waste| PHOTO[Request Photo of Waste]
    PHOTO --> CLASSIFY[Call POST /classify Bedrock Vision]
    
    CLASSIFY --> HAZARD_CHECK{Contains Hazardous<br/>battery / e-waste?}
    HAZARD_CHECK -- Yes --> DROP_OFF[Route to Authorised Drop-off Centre]
    HAZARD_CHECK -- No --> CONFIRM_ITEMS[Present Items & Reuse Tip]

    CONFIRM_ITEMS --> WEIGHT[Ask Approximate Weight]
    WEIGHT --> PRICE[Fetch GET /price<br/>Format: estimated Rs X-Y]
    PRICE --> BOOK_ASK{Want Doorstep Pickup?}
    
    BOOK_ASK -- No --> DECLINED([User Declined / Polite Exit])
    BOOK_ASK -- Yes --> ADDR_SLOT[Collect Address & Time Slot]
    
    ADDR_SLOT --> EXPLICIT_CONFIRM{Explicit Yes/हाँ Received?}
    EXPLICIT_CONFIRM -- No / Ambiguous --> REPROMPT[Re-prompt Confirmation]
    REPROMPT --> EXPLICIT_CONFIRM
    EXPLICIT_CONFIRM -- Yes --> CREATE_PICKUP[POST /pickups -> status: requested]
    
    CREATE_PICKUP --> MATCH[GET /recyclers/match]
    MATCH --> VOICE_CALL[POST /voice/offer -> Call Recycler]
    VOICE_CALL --> WAIT_CALL{Recycler Pressed 1 or 2?}
    
    WAIT_CALL -- Pressed 1 (Accept) --> CONFIRMED[Status: CONFIRMED<br/>Send address to recycler<br/>Send confirmation to user]
    WAIT_CALL -- Pressed 2 / Timeout --> NEXT_REC{Try Next Recycler?<br/>Max 3 tries}
    NEXT_REC -- Yes --> VOICE_CALL
    NEXT_REC -- No --> QUEUE[Leave in Requested Status for Ops Dashboard]
```

### 1.2 E-Waste Flow
```mermaid
flowchart TD
    EW_START([E-waste Selected]) --> ITEM_TYPE[Ask Item Type: Phone, Laptop, TV, etc.]
    ITEM_TYPE --> AGE[Ask Item Age: <2 yrs, 2-5 yrs, >5 yrs]
    AGE --> COND[Ask Condition: Working, Damaged, Broken]
    
    COND --> QUOTE[POST /ewaste/quote]
    QUOTE --> REC_DECISION{Recommendation}
    
    REC_DECISION -- "sell" --> SHOW_BUYERS[Show Authorised E-waste Buyers<br/>Estimate: Rs X-Y]
    REC_DECISION -- "dropoff" --> SHOW_DROPOFF[Show Nearest Authorised Drop-off Points]
    
    SHOW_BUYERS --> FINISH([End Flow])
    SHOW_DROPOFF --> FINISH
```

---

## 2. Hard Python Guardrails

The conversation relies on programmatic guardrails enforced in code (`agent/agent_core.py`), ensuring safety regardless of LLM generation:

1. **Confirmation Gating**:
   - `create_pickup` is blocked until `confirmed=True`.
   - The flag is only flipped when user input matches `_YES_PATTERN` (`yes`, `yep`, `हाँ`, `haan`, `theek hai`, etc.).
2. **Hazardous Label Blocking**:
   - Any pickup containing labels in `HAZARDOUS_LABELS` (`battery`, `ewaste`) is blocked by the agent and rejected with `422 Unprocessable Entity` by the backend.
3. **Price Normalisation**:
   - Enforced by regex transformation to guarantee the presence of the word "estimated" (e.g. `estimated Rs 120-180`).
4. **Address Secrecy**:
   - Address fields are never sent in the recycler matching payload or voice offer payload. They are sent via WhatsApp only after the status is `confirmed`.

---

## 3. Bilingual Template Specifications

All message strings are maintained in `conversation/templates.json` under strict limits:
- **Maximum characters**: 300 characters per text bubble.
- **Button limits**: Up to 3 quick-reply buttons (title $\le 20$ chars).
- **List picker**: Up to 10 rows (title $\le 24$ chars).
- **Recycler phone call**: Script length $< 25$ seconds in both English and Hindi.

