"""Checks the conversation design files (conversation/flow.json, conversation/templates.json).

Enforces the project rules so a copy edit can't silently break them:
  * every message exists in English and Hindi and is <= 300 characters once filled in
  * WhatsApp limits: <= 3 buttons, button titles <= 20 chars, list rows <= 10 / <= 24 chars
  * every message that shows a price carries the unmissable ESTIMATE label
  * the flow only references templates, buttons and states that exist
  * the recycler voice offer fits in 25 seconds and never mentions the address
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "conversation"
FLOW = json.loads((ROOT / "flow.json").read_text(encoding="utf-8"))
TPL = json.loads((ROOT / "templates.json").read_text(encoding="utf-8"))
TEMPLATES: dict = TPL["templates"]
RULES: dict = TPL["rules"]
LANGS = ("en", "hi")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def render(text: str, lang: str, samples: dict) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        assert key in samples, f"no sample value for placeholder {{{key}}}"
        value = samples[key]
        return value[lang] if isinstance(value, dict) else str(value)

    return PLACEHOLDER.sub(sub, text)


def title(entry: dict, lang: str) -> str:
    t = entry["title"]
    return t[lang] if isinstance(t, dict) else t


# ── templates ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_template_bilingual_and_under_limit(name: str) -> None:
    tpl = TEMPLATES[name]
    for lang in LANGS:
        body = tpl["body"].get(lang)
        assert body, f"{name}: missing {lang} body"
        rendered = render(body, lang, TPL["samples"])
        assert len(rendered) <= RULES["body_max_chars"], (
            f"{name}[{lang}] is {len(rendered)} chars (> {RULES['body_max_chars']}):\n{rendered}"
        )


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_whatsapp_interactive_limits(name: str) -> None:
    tpl = TEMPLATES[name]
    buttons = tpl.get("buttons", [])
    assert len(buttons) <= RULES["max_buttons"], f"{name}: too many buttons"
    assert len({b["id"] for b in buttons}) == len(buttons), f"{name}: duplicate button ids"
    if tpl["type"] == "buttons":
        assert buttons, f"{name}: type 'buttons' needs at least one button"
    for b in buttons:
        for lang in LANGS:
            t = title(b, lang)
            assert 0 < len(t) <= RULES["button_title_max_chars"], f"{name}.{b['id']}[{lang}] title too long: {t!r}"
    rows = tpl.get("rows", [])
    assert len(rows) <= 10, f"{name}: WhatsApp lists allow at most 10 rows"
    for r in rows:
        for lang in LANGS:
            assert len(title(r, lang)) <= RULES["list_row_title_max_chars"], f"{name}.{r['id']}[{lang}] row too long"
            desc = r.get("description", {}).get(lang, "")
            assert len(desc) <= RULES["list_row_description_max_chars"]
    if tpl["type"] == "list":
        for lang in LANGS:
            assert len(tpl["list_button"][lang]) <= 20
        gen = tpl.get("rows_generator")
        if gen:
            assert gen["days"] * len(gen["windows"]) <= 10


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_prices_always_labelled_as_estimate(name: str) -> None:
    tpl = TEMPLATES[name]
    shows_price = any("{low}" in tpl["body"][lang] or "{high}" in tpl["body"][lang] for lang in LANGS)
    assert shows_price == bool(tpl.get("contains_price")), f"{name}: contains_price flag is wrong"
    if not shows_price:
        return
    for lang in LANGS:
        rendered = render(tpl["body"][lang], lang, TPL["samples"])
        label = render(RULES["estimate_label"][lang], lang, TPL["samples"])
        assert label in rendered, f"{name}[{lang}] must contain the estimate label {label!r}"
    # English keeps the agent's canonical "estimated Rs X-Y"; Hindi must not trigger the
    # agent's English rewrite (a raw Rs/₹ followed by digits).
    assert "estimated Rs {low}-{high}" in tpl["body"]["en"]
    assert not re.search(r"(?:Rs\.?|₹)\s*\{low\}", tpl["body"]["hi"])


def test_recycler_message_only_after_accept() -> None:
    tpl = TEMPLATES["recycler_pickup_details"]
    assert tpl["audience"] == "recycler"
    assert tpl["send_only_when"] == "pickup.status == 'confirmed'"
    # Nothing else addressed to a recycler may carry the address.
    for name, t in TEMPLATES.items():
        if name == "recycler_pickup_details":
            continue
        for lang in LANGS:
            assert "{address_text}" not in t["body"][lang], f"{name} leaks address_text"


# ── flow ───────────────────────────────────────────────────────────────────────
def _walk(node, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("send", "also_send_to_recycler") and isinstance(v, str):
                out.append(v)
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)
    elif isinstance(node, str) and node.startswith("send:"):
        out.append(node[5:].split(" ")[0])


def test_flow_references_existing_templates() -> None:
    refs: list[str] = []
    _walk(FLOW["states"], refs)
    _walk(FLOW["global_transitions"], refs)
    refs += [FLOW["silence_policy"]["nudge_template"], FLOW["silence_policy"]["expire_template"]]
    missing = sorted(set(refs) - set(TEMPLATES))
    assert not missing, f"flow references unknown templates: {missing}"
    unused = sorted(set(TEMPLATES) - set(refs))
    assert not unused, f"templates never used by the flow: {unused}"


def test_flow_targets_exist() -> None:
    states = FLOW["states"]
    assert FLOW["initial_state"] in states
    for name, st in states.items():
        for t in st.get("transitions", []):
            to = t["to"]
            assert to in states or to in ("@same", "@return"), f"{name}: unknown target {to}"
        pre = st.get("precondition")
        if pre:
            assert pre["else_goto"] in states


def test_flow_buttons_exist_in_templates() -> None:
    ids = {b["id"] for t in TEMPLATES.values() for b in t.get("buttons", [])}
    rows = {r["id"] for t in TEMPLATES.values() for r in t.get("rows", [])}
    for name, st in FLOW["states"].items():
        for t in st.get("transitions", []):
            on = t["on"]
            if on.startswith("button:"):
                assert on[7:] in ids, f"{name}: no template defines button {on}"
            if on.startswith("list:") and not on.endswith("*"):
                assert on[5:] in rows, f"{name}: no template defines list row {on}"


def test_booking_requires_explicit_confirmation() -> None:
    """Only pickup_confirm may set confirmed=true, and only on an explicit yes."""
    for name, st in FLOW["states"].items():
        for t in st.get("transitions", []):
            sets_confirmed = "set:confirmed=true" in t.get("actions", [])
            if sets_confirmed:
                assert name == "pickup_confirm", f"{name} must not set confirmed=true"
                assert t["on"] in ("button:confirm_yes", "intent:yes")
            if t["to"] == "creating_pickup" and name != "creating_pickup":
                assert sets_confirmed, f"{name} reaches creating_pickup without confirmation"


def test_hazardous_items_never_reach_pickup() -> None:
    classify = FLOW["states"]["classifying"]["transitions"]
    hazard_idx = next(i for i, t in enumerate(classify) if t["to"] == "hazard_dropoff")
    confirm_idx = next(i for i, t in enumerate(classify) if t["to"] == "confirm_items")
    assert hazard_idx < confirm_idx, "hazard check must run before items are confirmed"
    ew = FLOW["states"]["ewaste_item"]["transitions"]
    assert any(t["on"] == "list:ew_battery" and t["to"] == "hazard_dropoff" for t in ew)
    assert "hazardous" in FLOW["states"]["creating_pickup"]["guard_required"]


def test_edge_cases_documented() -> None:
    for key in ("blurry_photo", "unknown_item", "user_says_no", "user_goes_silent", "hazardous_item"):
        case = FLOW["edge_cases"][key]
        for s in case["states"]:
            assert s == "*" or s in FLOW["states"], f"{key}: unknown state {s}"


# ── voice ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", LANGS)
def test_voice_offer_under_25_seconds(lang: str) -> None:
    voice = TPL["voice"]
    script = render(voice["scripts"]["offer"][lang], lang, voice["samples"])
    words = len(script.split())
    seconds = words / voice["speaking_rate_words_per_second"][lang]
    # The offer plus the one repeat prompt (if the recycler doesn't press) must fit.
    assert seconds <= voice["max_seconds"], f"{lang} offer is ~{seconds:.1f}s ({words} words)"
    assert "1" in script and "2" in script
    for forbidden in ("{address", "address", "पता"):
        assert forbidden not in voice["scripts"]["offer"][lang]
    for name, s in voice["scripts"].items():
        assert s[lang], f"voice.{name} missing {lang}"

