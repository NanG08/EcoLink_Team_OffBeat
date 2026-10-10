# Pricing & E-waste Quote — Assumptions

> **Every number in `rate_table.json` is currently a PLACEHOLDER** (`"is_placeholder": true`).
> They are indicative demo values, not rates collected from the market. Users only ever see
> them labelled as estimates (`🏷️ ESTIMATE · estimated Rs X-Y`), and the final price is always
> set by the kabadiwala or buyer after inspection.

EcoLink has **no partnership, tie-up or buy-back arrangement with any store, brand or
platform**. Quotes are rule-of-thumb estimates computed by EcoLink. The buyers and drop-off
points shown are independent recyclers from the recyclers table.

Data: [`rate_table.json`](rate_table.json) · Code: [`rates.py`](rates.py), which calls the
deployed backend's own rules in `backend/ecolink/core/pricing.py` · Load into the deployed
API: [`seed_rates.py`](seed_rates.py)

## 1. Scrap price — `GET /price?label=&weight_kg=`

`low = round(low_per_kg × weight_kg)`, `high = max(low, round(high_per_kg × weight_kg))`,
response `{low, high, currency: "INR", updated}`, where `updated` is the date of the rate data.

| Label | Low ₹/kg | High ₹/kg | Status | Note |
|-------|---------:|----------:|--------|------|
| paper | 10 | 14 | placeholder | Newspaper usually fetches the upper end |
| cardboard | 6 | 9 | placeholder | Must be dry and flattened |
| plastic | 8 | 18 | placeholder | Clean PET at the top, mixed/soft plastic at the bottom |
| metal | 30 | 60 | placeholder | Blended iron/steel; aluminium and copper are worth far more |
| glass | 1 | 3 | placeholder | Often taken for little or nothing |
| ewaste | — | — | not per kg | Use `POST /ewaste/quote` |
| battery | — | — | never priced | Hazardous → authorised drop-off only (HTTP 422 `hazardous_label`) |

Inputs: `weight_kg` must be > 0 and ≤ 1000, and `label` must be one of the 7 contract labels.

## 2. E-waste quote — `POST /ewaste/quote {item_type, age_years, condition}`

```
age_factor       = max(0.05, 1 − yearly_depreciation × age_years)
combined_factor  = age_factor × condition_factor
low  = round10(base_low  × combined_factor)
high = max(low, round10(base_high × combined_factor))
recommendation   = "sell" if high ≥ 500 else "dropoff"
```
`round10` rounds to the nearest ₹10 once the amount is ₹100 or more.

### 2a. Item table (like-new working resale range)

| item_type (after aliasing) | base_low ₹ | base_high ₹ | yearly depreciation | Status |
|---|---:|---:|---:|---|
| mobile | 8000 | 20000 | 0.20 | placeholder |
| laptop | 15000 | 35000 | 0.15 | placeholder |
| tablet | 6000 | 18000 | 0.18 | placeholder |
| tv | 6000 | 15000 | 0.12 | placeholder |
| monitor | 3000 | 8000 | 0.12 | placeholder |
| refrigerator | 8000 | 20000 | 0.08 | placeholder |
| washing_machine | 7000 | 16000 | 0.08 | placeholder |
| ac | 9000 | 22000 | 0.08 | placeholder |
| printer | 2000 | 6000 | 0.15 | placeholder |
| other (fallback for anything else) | 500 | 1500 | 0.20 | placeholder |

Aliases, from the backend: phone/smartphone/mobile phone/cellphone → mobile · notebook → laptop ·
television → tv · fridge → refrigerator · air conditioner → ac · washer → washing_machine ·
ipad → tablet · computer monitor → monitor.

### 2b. Condition factors

| condition | factor | accepted synonyms |
|---|---:|---|
| working | 1.00 | good, ok, fine |
| damaged | 0.35 | cracked, scratched, partial |
| broken | 0.08 | dead, faulty, not working |

Any other condition value → HTTP 400.

### 2c. Rules

| Rule | Value | Why |
|---|---|---|
| Age floor | 5% of base value | A very old device still has parts/metal value |
| Sell threshold | upper bound ≥ ₹500 | Below that, the trip to a buyer is not worth it; drop-off is safer |
| Batteries & power banks | always `{0, 0, "dropoff"}` | Hazardous: never sold through EcoLink |
| Unknown item types | use the `other` row | Conservative, low value |
| `age_years` | 0–50, fractional allowed | Input validation |

### 2d. Worked examples (computed by `rates.explain_quote`)

| Request | age × condition factor | Quote | Recommendation |
|---|---|---|---|
| mobile, 1 yr, working | 0.80 × 1.00 | ₹6,400–16,000 | sell |
| mobile, 3 yrs, damaged | 0.40 × 0.35 | ₹1,120–2,800 | sell |
| laptop, 2 yrs, working | 0.70 × 1.00 | ₹10,500–24,500 | sell |
| tv, 5 yrs, working | 0.40 × 1.00 | ₹2,400–6,000 | sell |
| toaster (→ other), 1 yr, working | 0.80 × 1.00 | ₹400–1,200 | sell |
| printer, 6 yrs, damaged | 0.10 × 0.35 | ₹70–210 | dropoff |
| laptop, 9 yrs, broken | 0.05 × 0.08 | ₹60–140 | dropoff |
| power bank, any | — | ₹0–0 | dropoff |

### 2e. Known limitations (stated honestly)

* "sell" is decided on the **upper** bound only, so ₹400–1,200 is "sell" although the lower
  bound is below ₹500.
* Linear depreciation with a 5% floor means a 10-year-old *working* phone still quotes
  ₹400–1,000. Real resale value of very old phones is often near zero.
* Brand, model, storage and accessories are ignored. One row covers every phone.
* Condition is self-reported by the user and never verified.

## 3. Replacing placeholders

1. For each scrap label, ask 3–5 kabadiwalas in the target area for their buying rate per kg. Record the
   lowest and highest *typical* figures (drop outliers), plus the date and area.
2. For each e-waste item, take like-new resale ranges from at least 3 local repair/resale shops or
   listings, and estimate depreciation from 1-, 3- and 5-year-old listings.
3. Edit `rate_table.json`: change the values, set `"placeholder": false` on each row you replaced,
   set `"updated"` to the collection date, and rewrite `"source"` (who/where/when, e.g.
   "5 kabadiwalas, Indiranagar, Bengaluru, 2026-11-02"). Set `"is_placeholder": false` only when
   every row is real.
4. `python ml/pricing/seed_rates.py --dry-run` (validates), then without `--dry-run` to load it into
   DynamoDB, and `python -m pytest tests/test_pricing_rate_table.py`.
