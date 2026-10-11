PROJECT: EcoLink, a WhatsApp-first assistant for household waste. Hackathon: WeMakeDevs x AWS, 4 days. Deploy on AWS.

FLOW: A user chats on WhatsApp (English or Hindi) and picks "Regular waste" or "E-waste".
- Regular: send a photo -> classify items -> short reuse/recycle tip + estimated price range -> if the user wants pickup, match a nearby recycler (kabadiwala) -> system phones the recycler, who presses 1 to accept or 2 to decline -> both sides get a confirmation message.
- E-waste: ask item, age, condition -> estimated resale range -> if worth selling, show sell options; otherwise show nearest authorised e-waste drop-off points. Batteries and hazardous items ALWAYS go to safe drop-off, never a normal pickup.

RULES: never book without explicit user confirmation; label all prices as estimates; share a household's address with a recycler only after the recycler accepts; keep images private and expire them after 7 days.

STACK: Python 3.12 backend, AWS (Lambda, API Gateway, DynamoDB, S3, SNS, Cognito), Strands Agents SDK for the agent. Messaging and voice providers must sit behind an interface so they can be swapped between AWS services and sandbox providers.

API CONTRACT (all JSON; do not change field names):
- POST /classify {image_url} -> {items:[{label, confidence, hazardous}]}; labels: paper, cardboard, plastic, metal, glass, ewaste, battery
- GET /price?label=&weight_kg= -> {low, high, currency:"INR", updated}
- POST /ewaste/quote {item_type, age_years, condition} -> {low, high, recommendation:"sell"|"dropoff"}
- GET /recyclers/match?lat=&lng=&labels= -> [{recycler_id, name, distance_km, accepts:[labels]}]
- POST /pickups {user_id, items, address_text, slot} -> {pickup_id}
- PATCH /pickups/{id} {status} ; statuses: requested, offered, confirmed, completed, cancelled, declined
- POST /agent/message {user_id, text, image_url?, lang} -> {reply_text, actions:[...]}
- POST /voice/offer {pickup_id, recycler_id}; callback POST /voice/response {pickup_id, digit}