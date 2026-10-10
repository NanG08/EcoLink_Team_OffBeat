"""Demo data shared by seed.py and reset.py: 8 recyclers, a sample user and the rate table.

Recycler phone numbers and the sample user are NOT hard-coded: pass real numbers on the
command line so that the voice call and WhatsApp messages actually reach a phone.
Locations are around central Bengaluru (12.9716, 77.5946).
"""

from __future__ import annotations

from datetime import date

BASE_LAT, BASE_LNG = 12.9716, 77.5946

# (id, name, d_lat, d_lng, accepts, available, lang)
_RECYCLERS = [
    ("R001", "Sharma Kabadi Works", 0.011, 0.004, ["paper", "cardboard", "plastic", "metal", "glass"], True, "hi"),
    ("R002", "Gupta Scrap Traders", 0.022, -0.010, ["paper", "cardboard", "plastic"], True, "hi"),
    ("R003", "Metro Metal Mart", -0.018, 0.015, ["metal"], True, "en"),
    ("R004", "Green Loop Recyclers", 0.030, 0.020, ["paper", "cardboard", "plastic", "glass", "metal"], True, "en"),
    ("R005", "City E-Waste Hub (authorised)", -0.025, -0.012, ["ewaste", "battery"], True, "en"),
    ("R006", "Khan Glass & Bottle", 0.008, -0.022, ["glass", "plastic"], True, "hi"),
    ("R007", "Eco Drop Point (authorised)", 0.041, -0.030, ["ewaste", "battery"], True, "en"),
    # Deliberately unavailable: exercises the availability filter in /recyclers/match.
    ("R008", "Sunrise Scrap (closed today)", 0.004, 0.003, ["paper", "cardboard", "plastic", "metal", "glass"], False, "hi"),
]


def recyclers(phone: str) -> list[dict]:
    return [
        {
            "recycler_id": rid, "name": name,
            "lat": round(BASE_LAT + dlat, 5), "lng": round(BASE_LNG + dlng, 5),
            "accepts": accepts, "available": available, "lang": lang, "phone": phone,
        }
        for rid, name, dlat, dlng, accepts, available, lang in _RECYCLERS
    ]


def sample_user(user_id: str, lat: float = BASE_LAT, lng: float = BASE_LNG) -> dict:
    return {"user_id": user_id, "name": "Demo Household", "lang": "en", "lat": lat, "lng": lng}


def rate_rows() -> list[dict]:
    """rate_table rows. Scrap rates are Rs/kg ranges; e-waste rows hold a 'like-new' resale
    range that is depreciated by age and condition (see ecolink/core/pricing.py)."""
    today = date.today().isoformat()
    scrap = {  # label: (low, high) Rs per kg -- indicative kabadiwala rates
        "paper": (10, 14), "cardboard": (6, 9), "plastic": (8, 18), "metal": (30, 60), "glass": (1, 3),
    }
    ewaste = {  # item: (base_low, base_high, yearly_depreciation)
        "mobile": (8000, 20000, 0.20), "laptop": (15000, 35000, 0.15), "tablet": (6000, 18000, 0.18),
        "tv": (6000, 15000, 0.12), "monitor": (3000, 8000, 0.12), "refrigerator": (8000, 20000, 0.08),
        "washing_machine": (7000, 16000, 0.08), "ac": (9000, 22000, 0.08), "printer": (2000, 6000, 0.15),
        "other": (500, 1500, 0.20),
    }
    conditions = {"working": 1.0, "damaged": 0.35, "broken": 0.08}
    rows = [{"kind": "scrap", "item": k, "low_per_kg": lo, "high_per_kg": hi, "updated": today}
            for k, (lo, hi) in scrap.items()]
    rows += [{"kind": "ewaste", "item": k, "base_low": lo, "base_high": hi,
              "yearly_depreciation": dep, "updated": today} for k, (lo, hi, dep) in ewaste.items()]
    rows += [{"kind": "ewaste_condition", "item": k, "factor": f} for k, f in conditions.items()]
    return rows
