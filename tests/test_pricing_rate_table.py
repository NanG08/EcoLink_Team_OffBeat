"""
Tests for ml/pricing: rate_table.json, price_estimate / ewaste_quote / explain_quote,
seed_rates, and parity with the deployed backend GET /price and POST /ewaste/quote.
"""
import copy
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.pricing import rates
from ml.pricing.rates import RateTableError, validate

from ecolink.core import pricing as backend_pricing  # noqa: E402  (rates puts backend/ on sys.path)
from ecolink.core.http import ApiError  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_RAW = json.loads(rates.RATE_TABLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw():
    return copy.deepcopy(_RAW)


# ── rate_table.json content ───────────────────────────────────────────────────

def test_rate_table_loads_and_validates() -> None:
    table = rates.load()
    assert table.raw["currency"] == "INR"
    assert len(table.dynamo_rows()) == 5 + len(_RAW["ewaste"]["items"]) + 3


def test_placeholders_are_clearly_marked() -> None:
    rows = [r for k, r in _RAW["scrap"].items() if not k.startswith("_")] + list(_RAW["ewaste"]["items"].values())
    assert all("placeholder" in r for r in rows), "every rate row must say whether it is a placeholder"
    if any(r["placeholder"] for r in rows):
        assert _RAW["is_placeholder"] is True
        assert "PLACEHOLDER" in _RAW["source"]
    if _RAW["is_placeholder"] is False:
        assert not any(r["placeholder"] for r in rows), "is_placeholder=false but some rows are still placeholders"


def test_every_regular_label_is_priced_and_hazardous_ones_explained() -> None:
    assert set(k for k in _RAW["scrap"] if not k.startswith("_")) == set(rates.REGULAR_LABELS)
    assert set(_RAW["not_priced_per_kg"]) == {"ewaste", "battery"}


def test_updated_is_an_iso_date_not_in_the_future() -> None:
    assert date.fromisoformat(_RAW["updated"]) <= date.today()


def test_documented_rules_mirror_backend_constants() -> None:
    rules = _RAW["ewaste"]["rules"]
    assert rules["min_age_factor"] == backend_pricing.MIN_AGE_FACTOR
    assert rules["sell_threshold_inr"] == backend_pricing.SELL_THRESHOLD_INR
    assert set(_RAW["ewaste"]["condition_factors"]) == set(backend_pricing.CONDITIONS)


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r["scrap"]["paper"].update(low_per_kg=20, high_per_kg=10), "paper"),
    (lambda r: r["scrap"].pop("metal"), "missing for 'metal'"),
    (lambda r: r["scrap"].update(battery={"low_per_kg": 1, "high_per_kg": 2}), "battery"),
    (lambda r: r.update(updated=(date.today() + timedelta(days=2)).isoformat()), "future"),
    (lambda r: r.update(updated="10/10/2026"), "ISO date"),
    (lambda r: r.update(source="  "), "source"),
    (lambda r: r.update(currency="USD"), "INR"),
    (lambda r: r["ewaste"]["items"]["tv"].update(yearly_depreciation=1.5), "yearly_depreciation"),
    (lambda r: r["ewaste"]["items"].pop("other"), "'other'"),
    (lambda r: r["ewaste"]["condition_factors"].pop("broken"), "condition_factors"),
    (lambda r: r["not_priced_per_kg"].pop("battery"), "'battery'"),
])
def test_validate_rejects_bad_tables(raw, mutate, needle) -> None:
    mutate(raw)
    with pytest.raises(RateTableError, match=re.escape(needle)):
        validate(raw)


# ── GET /price function ───────────────────────────────────────────────────────

def test_price_estimate_contract_and_values() -> None:
    out = rates.price_estimate("paper", 3)
    assert out == {"low": 30, "high": 42, "currency": "INR", "updated": _RAW["updated"]}
    assert rates.price_estimate(" Metal ", "2.5") == {"low": 75, "high": 150, "currency": "INR", "updated": _RAW["updated"]}


@pytest.mark.parametrize("label,weight,status,code", [
    ("gold", 1, 400, "invalid_request"),
    ("paper", 0, 400, "invalid_request"),
    ("paper", 1001, 400, "invalid_request"),
    ("paper", "abc", 400, "invalid_request"),
    ("paper", None, 400, "invalid_request"),
    ("battery", 1, 422, "hazardous_label"),
    ("ewaste", 1, 422, "hazardous_label"),
])
def test_price_estimate_errors_match_backend(label, weight, status, code) -> None:
    with pytest.raises(ApiError) as exc:
        rates.price_estimate(label, weight)
    assert (exc.value.status, exc.value.code) == (status, code)


# ── POST /ewaste/quote function ───────────────────────────────────────────────

@pytest.mark.parametrize("args,expected", [
    (("Laptop", 2, "working"), {"low": 10500, "high": 24500, "recommendation": "sell"}),
    (("laptop", 9, "broken"), {"low": 60, "high": 140, "recommendation": "dropoff"}),
    (("smartphone", 3, "cracked"), {"low": 1120, "high": 2800, "recommendation": "sell"}),
    (("toaster", 1, "working"), {"low": 400, "high": 1200, "recommendation": "sell"}),
    (("printer", 6, "damaged"), {"low": 70, "high": 210, "recommendation": "dropoff"}),
])
def test_ewaste_quote_values(args, expected) -> None:
    assert rates.ewaste_quote(*args) == expected


@pytest.mark.parametrize("item", ["Lithium battery", "AA batteries", "power bank", "powerbank"])
def test_batteries_always_dropoff(item) -> None:
    assert rates.ewaste_quote(item, 0, "working") == {"low": 0, "high": 0, "recommendation": "dropoff"}


@pytest.mark.parametrize("args", [("laptop", 1, "haunted"), ("laptop", -1, "working"), ("", 1, "working"),
                                  ("laptop", 51, "working"), ("laptop", "x", "working")])
def test_ewaste_quote_rejects_bad_input(args) -> None:
    with pytest.raises(ApiError) as exc:
        rates.ewaste_quote(*args)
    assert exc.value.status == 400


def test_explain_quote_is_consistent_and_transparent() -> None:
    for item in list(_RAW["ewaste"]["items"]) + ["phone", "fridge", "toaster"]:
        for age in (0, 1, 3.5, 10, 30):
            for cond in ("working", "damaged", "broken"):
                q = rates.explain_quote(item, age, cond)
                assert rates.ewaste_quote(item, age, cond) == {k: q[k] for k in ("low", "high", "recommendation")}
                assert q["age_factor"] >= backend_pricing.MIN_AGE_FACTOR
                assert q["combined_factor"] == pytest.approx(q["age_factor"] * q["condition_factor"], abs=1e-3)
                assert q["low"] <= q["high"]
                assert q["recommendation"] == ("sell" if q["high"] >= q["sell_threshold_inr"] else "dropoff")
    assert rates.explain_quote("toaster", 1, "working")["rate_row"] == "other"


# ── docs stay in sync, no partnership claims ──────────────────────────────────

def test_assumptions_doc_lists_every_rate() -> None:
    doc = (_ROOT / "ml" / "pricing" / "ASSUMPTIONS.md").read_text(encoding="utf-8")
    for label in rates.REGULAR_LABELS:
        r = _RAW["scrap"][label]
        assert re.search(rf"\| {label} \| {r['low_per_kg']} \| {r['high_per_kg']} \|", doc), label
    for item, r in _RAW["ewaste"]["items"].items():
        assert re.search(rf"\| {item}\b[^|]*\| {r['base_low']} \| {r['base_high']} \| {r['yearly_depreciation']:.2f} \|", doc), item
    for cond, f in _RAW["ewaste"]["condition_factors"].items():
        assert f"| {cond} | {f:.2f} |" in doc, cond
    assert "no partnership" in doc.lower()


def test_user_facing_text_claims_no_store_partnership() -> None:
    texts = [(_ROOT / "conversation" / "templates.json").read_text(encoding="utf-8"), json.dumps(_RAW)]
    banned = r"\b(partner(ed|ship)?|tie[- ]?up|official buy-?back|cashify|olx|quikr|flipkart|amazon exchange)\b"
    for text in texts:
        assert not re.search(banned, text, re.IGNORECASE), re.search(banned, text, re.IGNORECASE).group(0)


# ── seed_rates -> DynamoDB -> deployed backend endpoints ─────────────────────

class TestBackendParity:
    """The real backend handlers, served from rows written by seed_rates, give identical answers."""

    @pytest.fixture(autouse=True)
    def _aws(self):
        from tests.support import AwsTestCase

        case = AwsTestCase()
        case.seed_data = False
        case.setUp()
        from ml.pricing.seed_rates import seed_rates

        assert seed_rates(case.ddb) == len(rates.load().dynamo_rows())
        yield
        case.doCleanups()

    @staticmethod
    def _call(method, resource, body=None, query=None):
        from ecolink.handlers import pricing as pricing_handler
        from tests.support import api_event, invoke

        return invoke(pricing_handler.handler, api_event(method, resource, body, query))

    def test_price_endpoint_serves_rate_table_with_its_real_date(self) -> None:
        for label in rates.REGULAR_LABELS:
            for weight in ("0.4", "1", "3.7", "25"):
                status, body = self._call("GET", "/price", query={"label": label, "weight_kg": weight})
                assert status == 200
                assert body == rates.price_estimate(label, weight)
                assert body["updated"] == _RAW["updated"]   # not the date the table was seeded

    def test_quote_endpoint_matches_rate_table(self) -> None:
        for item in ["mobile", "laptop", "tv", "fridge", "toaster", "power bank"]:
            for age in (0, 2, 7):
                for cond in ("working", "damaged", "broken"):
                    status, body = self._call("POST", "/ewaste/quote",
                                              {"item_type": item, "age_years": age, "condition": cond})
                    assert status == 200
                    assert body == rates.ewaste_quote(item, age, cond)
