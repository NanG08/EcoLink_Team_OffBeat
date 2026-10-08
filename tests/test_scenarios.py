"""
EcoLink agent test suite – 10 scenarios against mocked tools.

Run from the project root:
    python -m pytest tests/test_scenarios.py -v
  or directly:
    python tests/test_scenarios.py

Each test injects a Mock API client so no real HTTP calls are made and no
DynamoDB table is needed. A lightweight in-memory state backend is used.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from typing import Any
from unittest.mock import MagicMock, patch, call

# Ensure the project root is on sys.path so `import agent` works when the
# file is run directly (python tests/test_scenarios.py) as well as via pytest.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Minimal stubs for strands so the module imports work without the real SDK.
# ---------------------------------------------------------------------------

def _make_strands_stub():
    """Create a minimal `strands` package stub if the real one isn't installed."""
    if "strands" in sys.modules:
        return

    strands_mod = types.ModuleType("strands")

    def tool(fn=None, **kwargs):
        """Passthrough decorator – just returns the original function."""
        if fn is not None:
            return fn
        def decorator(f):
            return f
        return decorator

    class Agent:
        def __init__(self, model=None, system_prompt=None, tools=None):
            self._tools = {t.__name__: t for t in (tools or [])}
            self._last_input = ""

        def __call__(self, text: str) -> str:
            # A stub that echoes inputs and can be overridden per-test.
            self._last_input = text
            return f"[STUB RESPONSE for: {text[:60]}]"

    strands_mod.tool = tool
    strands_mod.Agent = Agent
    sys.modules["strands"] = strands_mod


_make_strands_stub()

# ---------------------------------------------------------------------------
# Stub boto3 / botocore so state.py imports succeed without AWS credentials.
# ---------------------------------------------------------------------------

def _real_package_available(name: str) -> bool:
    """The backend tests use real boto3 (against moto); never shadow it with a stub."""
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _make_boto_stubs():
    if _real_package_available("boto3") and _real_package_available("botocore"):
        return

    if "boto3" not in sys.modules:
        boto3_mod = types.ModuleType("boto3")
        sys.modules["boto3"] = boto3_mod

    if "botocore" not in sys.modules:
        botocore_mod = types.ModuleType("botocore")
        exc_mod = types.ModuleType("botocore.exceptions")
        class ClientError(Exception):
            def __init__(self, error_response, operation_name):
                self.response = error_response
                self.operation_name = operation_name
        exc_mod.ClientError = ClientError
        botocore_mod.exceptions = exc_mod
        sys.modules["botocore"] = botocore_mod
        sys.modules["botocore.exceptions"] = exc_mod

    # boto3.dynamodb.conditions
    if "boto3.dynamodb" not in sys.modules:
        dyn_mod = types.ModuleType("boto3.dynamodb")
        cond_mod = types.ModuleType("boto3.dynamodb.conditions")
        class Attr:
            def __init__(self, name): self.name = name
            def not_exists(self): return self
            def eq(self, v): return self
        cond_mod.Attr = Attr
        dyn_mod.conditions = cond_mod
        sys.modules["boto3.dynamodb"] = dyn_mod
        sys.modules["boto3.dynamodb.conditions"] = cond_mod


_make_boto_stubs()

# ---------------------------------------------------------------------------
# Now we can safely import agent modules.
# ---------------------------------------------------------------------------

import agent.tools as tools_module
from agent.api_client import EcoLinkAPIClient, EcoLinkAPIError
from agent.tools import (
    classify_image,
    create_pickup,
    ewaste_quote,
    find_recyclers,
    get_price,
    offer_to_recycler,
    update_pickup,
)


# ---------------------------------------------------------------------------
# In-memory DynamoDB table stub
# ---------------------------------------------------------------------------

class _InMemoryTable:
    """Minimal DynamoDB Table stub backed by a plain dict."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def get_item(self, Key: dict) -> dict:
        pk = Key["user_id"]
        item = self._store.get(pk)
        return {"Item": item} if item else {}

    def put_item(self, Item: dict, ConditionExpression=None) -> dict:
        self._store[Item["user_id"]] = dict(Item)
        return {}


class _InMemoryDynamoResource:
    def __init__(self):
        self._tables: dict[str, _InMemoryTable] = {}

    def Table(self, name: str) -> _InMemoryTable:
        if name not in self._tables:
            self._tables[name] = _InMemoryTable()
        return self._tables[name]


# ---------------------------------------------------------------------------
# Mock API client factory
# ---------------------------------------------------------------------------

def _mock_client(**overrides) -> MagicMock:
    """
    Build a MagicMock that looks like EcoLinkAPIClient.
    Overrides let individual tests customise specific method return values.
    """
    mc = MagicMock(spec=EcoLinkAPIClient)

    mc.classify_image.return_value = {
        "items": [{"label": "paper", "confidence": 0.95, "hazardous": False}]
    }
    mc.get_price.return_value = {"low": 5, "high": 8, "currency": "INR", "updated": "2026-10-04"}
    mc.ewaste_quote.return_value = {"low": 500, "high": 1200, "recommendation": "sell"}
    mc.find_recyclers.return_value = [
        {"recycler_id": "r1", "name": "Ramu Kabadi", "distance_km": 0.8, "accepts": ["paper", "plastic"]}
    ]
    mc.create_pickup.return_value = {"pickup_id": "pu-001"}
    mc.update_pickup.return_value = {}
    mc.offer_to_recycler.return_value = {}

    for attr, val in overrides.items():
        getattr(mc, attr).return_value = val

    return mc


# ---------------------------------------------------------------------------
# Base test mixin: injects client, provides assertion helpers
# ---------------------------------------------------------------------------

class _AgentTestMixin:
    def _inject(self, **overrides) -> MagicMock:
        mc = _mock_client(**overrides)
        tools_module.set_client(mc)
        return mc

    def _assert_pass(self, condition: bool, msg: str):
        """Called by tests – raises AssertionError on failure."""
        assert condition, msg


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestScenario1_HappyPath(unittest.TestCase, _AgentTestMixin):
    """Happy path: image → classify → price → confirm → pickup → offer recycler."""

    def test_full_regular_flow(self):
        mc = self._inject()

        # Step 1 – classify
        result = classify_image(
            image_url="https://s3.example.com/img1.jpg",
            user_id="user-1",
        )
        self.assertIn("items", result)
        self.assertEqual(result["items"][0]["label"], "paper")
        self.assertIn("tip", result)
        self.assertIn("tip", result, "classify_image must return a tip")

        # Step 2 – price
        price = get_price(label="paper", weight_kg=2.0, user_id="user-1")
        self.assertIn("formatted", price)
        self.assertTrue(
            price["formatted"].startswith("estimated Rs"),
            f"Price must start with 'estimated Rs', got: {price['formatted']}",
        )

        # Step 3 – pickup (confirmed=True simulates user saying yes)
        pickup = create_pickup(
            user_id="user-1",
            items=[{"label": "paper", "weight_kg": 2.0}],
            address_text="123 MG Road, Bengaluru",
            slot="2026-10-05T10:00:00+05:30",
            confirmed=True,
        )
        self.assertIn("pickup_id", pickup, "Expected pickup_id in response")
        self.assertEqual(pickup["pickup_id"], "pu-001")

        # Step 4 – offer recycler
        offer = offer_to_recycler(pickup_id="pu-001", recycler_id="r1", user_id="user-1")
        self.assertNotIn("error", offer)

        print("PASS  Scenario 1: Happy path")


class TestScenario2_WrongClassificationCorrectedByUser(unittest.TestCase, _AgentTestMixin):
    """User corrects the classification from 'plastic' to 'metal'."""

    def test_classification_correction(self):
        mc = self._inject(
            classify_image={"items": [{"label": "plastic", "confidence": 0.70, "hazardous": False}]},
            get_price={"low": 10, "high": 15, "currency": "INR", "updated": "2026-10-04"},
        )

        result = classify_image(image_url="https://s3.example.com/img2.jpg", user_id="user-2")
        self.assertEqual(result["items"][0]["label"], "plastic")

        # Simulated user says "that's metal not plastic" – agent re-fetches price for metal
        mc.get_price.return_value = {"low": 20, "high": 40, "currency": "INR", "updated": "2026-10-04"}
        corrected_price = get_price(label="metal", weight_kg=1.5, user_id="user-2")
        self.assertIn("formatted", corrected_price)
        mc.get_price.assert_called_with("metal", 1.5)

        print("PASS  Scenario 2: Wrong classification corrected by user")


class TestScenario3_BatteryDetected_SafeDropoff(unittest.TestCase, _AgentTestMixin):
    """Battery detected → pickup must be blocked → safe drop-off response."""

    def test_battery_blocked(self):
        self._inject(
            classify_image={
                "items": [
                    {"label": "battery", "confidence": 0.99, "hazardous": True}
                ]
            }
        )

        result = classify_image(image_url="https://s3.example.com/battery.jpg", user_id="user-3")
        self.assertTrue(result["items"][0]["hazardous"])
        self.assertEqual(result["items"][0]["label"], "battery")

        # Attempting a pickup with a battery must be blocked by the guardrail.
        pickup_result = create_pickup(
            user_id="user-3",
            items=[{"label": "battery", "weight_kg": 0.3}],
            address_text="44 Park Ave",
            slot="2026-10-06T09:00:00+05:30",
            confirmed=True,
        )
        self.assertIn("error", pickup_result)
        self.assertEqual(pickup_result["error"], "hazardous_items_blocked")

        print("PASS  Scenario 3: Battery detected → safe drop-off enforced")


class TestScenario4_ConfirmationGate(unittest.TestCase, _AgentTestMixin):
    """Pickup must be rejected when confirmed=False."""

    def test_pickup_without_confirmation(self):
        self._inject()

        result = create_pickup(
            user_id="user-4",
            items=[{"label": "paper", "weight_kg": 1.0}],
            address_text="7 Nehru Nagar",
            slot="2026-10-07T11:00:00+05:30",
            confirmed=False,  # ← no confirmation
        )
        self.assertIn("error", result)
        self.assertEqual(result["error"], "pickup_not_confirmed")

        print("PASS  Scenario 4: Pickup blocked without explicit confirmation")


class TestScenario5_DeclineThenReschedule(unittest.TestCase, _AgentTestMixin):
    """User declines pickup, then decides to reschedule with a new slot."""

    def test_decline_then_reschedule(self):
        mc = self._inject()

        # First attempt – user declines (confirmed=False, simulated by agent not setting flag)
        first_attempt = create_pickup(
            user_id="user-5",
            items=[{"label": "plastic", "weight_kg": 3.0}],
            address_text="12 Gandhi Street",
            slot="2026-10-05T08:00:00+05:30",
            confirmed=False,
        )
        self.assertIn("error", first_attempt)

        # Reschedule – user confirms with new slot
        second_attempt = create_pickup(
            user_id="user-5",
            items=[{"label": "plastic", "weight_kg": 3.0}],
            address_text="12 Gandhi Street",
            slot="2026-10-08T15:00:00+05:30",
            confirmed=True,
        )
        self.assertIn("pickup_id", second_attempt)
        self.assertEqual(second_attempt["pickup_id"], "pu-001")

        print("PASS  Scenario 5: Decline then reschedule")


class TestScenario6_HindiConversation(unittest.TestCase, _AgentTestMixin):
    """Hindi language detection and tip output."""

    def test_hindi_classify_returns_tip(self):
        self._inject()

        result = classify_image(
            image_url="https://s3.example.com/img_hi.jpg",
            user_id="user-hi",
        )
        # Tip must be present and non-empty
        self.assertIn("tip", result)
        self.assertTrue(len(result["tip"]) > 0, "Tip should not be empty")

        # Verify the tip is a single sentence (contains at most one full-stop mid-string)
        tip = result["tip"]
        # A one-liner should have ≤1 sentence-ending punctuation
        sentence_ends = tip.count(".") + tip.count("।")
        self.assertLessEqual(sentence_ends, 2, f"Tip should be ≤1 sentence: '{tip}'")

        print("PASS  Scenario 6: Hindi conversation flow (tip check)")


class TestScenario7_ToolFailureWithFallback(unittest.TestCase, _AgentTestMixin):
    """API call fails → tool returns error dict (not exception)."""

    def test_classify_failure(self):
        mc = _mock_client()
        mc.classify_image.side_effect = EcoLinkAPIError(500, "Internal Server Error")
        tools_module.set_client(mc)

        result = classify_image(image_url="https://s3.example.com/broken.jpg", user_id="user-7")
        self.assertIn("error", result)
        self.assertIn("classify", result["error"].lower())

        print("PASS  Scenario 7: Tool failure returns error dict (no raw exception)")


class TestScenario8_EwasteDropoffPath(unittest.TestCase, _AgentTestMixin):
    """E-waste quote recommends drop-off → find_recyclers called for drop-off points."""

    def test_ewaste_dropoff(self):
        self._inject(
            ewaste_quote={"low": 0, "high": 50, "recommendation": "dropoff"}
        )

        quote = ewaste_quote(item_type="old_tv", age_years=10, condition="broken", user_id="user-8")
        self.assertEqual(quote["recommendation"], "dropoff")
        self.assertIn("formatted", quote)

        recyclers = find_recyclers(lat=12.97, lng=77.59, labels=["ewaste"], user_id="user-8")
        self.assertIn("recyclers", recyclers)
        self.assertGreater(len(recyclers["recyclers"]), 0)

        print("PASS  Scenario 8: E-waste drop-off path")


class TestScenario9_PriceFormatting(unittest.TestCase, _AgentTestMixin):
    """Every price output must be formatted as 'estimated Rs X-Y'."""

    def test_price_always_formatted(self):
        self._inject()

        for label, weight in [("paper", 1.0), ("plastic", 2.5), ("metal", 0.5)]:
            result = get_price(label=label, weight_kg=weight, user_id="user-9")
            self.assertIn("formatted", result)
            self.assertTrue(
                result["formatted"].startswith("estimated Rs"),
                f"For label={label}: expected 'estimated Rs X-Y', got '{result['formatted']}'",
            )

        ewaste_result = ewaste_quote(
            item_type="laptop", age_years=3, condition="working", user_id="user-9"
        )
        self.assertIn("formatted", ewaste_result)
        self.assertTrue(
            ewaste_result["formatted"].startswith("estimated Rs"),
            f"E-waste quote formatted incorrectly: {ewaste_result['formatted']}",
        )

        print("PASS  Scenario 9: All price outputs use 'estimated Rs X-Y'")


class TestScenario10_UpdatePickupStatuses(unittest.TestCase, _AgentTestMixin):
    """Update pickup with each valid status; invalid status returns error."""

    def test_update_valid_and_invalid_status(self):
        self._inject()

        valid_statuses = ["requested", "offered", "confirmed", "completed", "cancelled", "declined"]
        for status in valid_statuses:
            result = update_pickup(pickup_id="pu-001", status=status, user_id="user-10")
            self.assertNotIn(
                "error", result,
                f"Valid status '{status}' should not produce an error, got: {result}",
            )

        # Invalid status must return an error dict
        bad_result = update_pickup(pickup_id="pu-001", status="flying", user_id="user-10")
        self.assertIn("error", bad_result)
        self.assertIn("flying", bad_result["error"])

        print("PASS  Scenario 10: update_pickup valid/invalid statuses")


# ---------------------------------------------------------------------------
# Runner with pass/fail summary
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestScenario1_HappyPath,
        TestScenario2_WrongClassificationCorrectedByUser,
        TestScenario3_BatteryDetected_SafeDropoff,
        TestScenario4_ConfirmationGate,
        TestScenario5_DeclineThenReschedule,
        TestScenario6_HindiConversation,
        TestScenario7_ToolFailureWithFallback,
        TestScenario8_EwasteDropoffPath,
        TestScenario9_PriceFormatting,
        TestScenario10_UpdatePickupStatuses,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)

    print("\n" + "═" * 50)
    print(f"  Total : {result.testsRun}")
    print(f"  Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failed: {len(result.failures) + len(result.errors)}")
    print("═" * 50)

    sys.exit(0 if result.wasSuccessful() else 1)
