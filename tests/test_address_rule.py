"""The address-sharing rule: no household address reaches a recycler before `confirmed`.

Pure-function tests cover the whitelist builders; flow tests drive the real handlers
(voice offer -> recycler keypad callback) and inspect what really left the system: the
form bodies Twilio received over HTTP and the events that went through SNS.
"""

from __future__ import annotations

import json
import unittest

from ecolink.core.constants import ALL_STATUSES
from ecolink.core.offers import AddressLeakError, assert_no_address, pickup_view, recycler_view
from ecolink.handlers import pickups as pickups_handler
from ecolink.handlers import voice as voice_handler
from tests.support import (HOUSEHOLD_ADDRESS, RECYCLER_PHONE, SLOT, USER_ID, AwsTestCase,
                           api_event, invoke, twilio_event)

ADDRESS_FRAGMENTS = [HOUSEHOLD_ADDRESS, "Rosewood", "MG Road", "Flat 4B"]


def make_pickup(status):
    return {"pickup_id": "pk_test", "user_id": USER_ID, "status": status, "slot": SLOT,
            "address_text": HOUSEHOLD_ADDRESS,
            "items": [{"label": "paper", "weight_kg": 2, "image_url": "https://s3/secret.jpg"}],
            "notes": "ring the bell twice"}


def leaks(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return [f for f in ADDRESS_FRAGMENTS if f in text]


class RecyclerViewTests(unittest.TestCase):
    def test_address_is_hidden_until_confirmed(self):
        for status in ("requested", "offered", "declined", "cancelled"):
            with self.subTest(status=status):
                view = recycler_view(make_pickup(status))
                self.assertNotIn("address_text", view)
                self.assertEqual(leaks(view), [])

    def test_address_is_included_once_confirmed_or_completed(self):
        for status in ("confirmed", "completed"):
            self.assertEqual(recycler_view(make_pickup(status))["address_text"], HOUSEHOLD_ADDRESS)

    def test_exactly_confirmed_and_completed_expose_the_address(self):
        visible = {s for s in ALL_STATUSES if "address_text" in recycler_view(make_pickup(s))}
        self.assertEqual(visible, {"confirmed", "completed"})

    def test_view_is_a_whitelist_not_a_copy(self):
        view = recycler_view(make_pickup("offered"))
        self.assertEqual(view["items"], [{"label": "paper", "weight_kg": 2}])  # image_url dropped
        self.assertEqual(set(view), {"pickup_id", "status", "items", "slot"})  # no user_id / notes

    def test_assert_no_address_guard(self):
        pickup = make_pickup("offered")
        assert_no_address(recycler_view(pickup), pickup)
        with self.assertRaises(AddressLeakError):
            assert_no_address({"address_text": HOUSEHOLD_ADDRESS}, pickup)
        with self.assertRaises(AddressLeakError):
            assert_no_address({"note": f"deliver to {HOUSEHOLD_ADDRESS.upper()}"}, pickup)

    def test_dashboard_view_for_non_admins(self):
        view = pickup_view(make_pickup("offered"), privileged=False)
        self.assertNotIn("address_text", view)
        self.assertNotIn("user_id", view)
        self.assertIn("address_text", pickup_view(make_pickup("confirmed"), privileged=False))
        self.assertIn("address_text", pickup_view(make_pickup("requested"), privileged=True))


class DashboardTests(AwsTestCase):
    def test_get_pickup_respects_cognito_groups(self):
        pid = self.new_pickup()

        def get(groups):
            ev = api_event("GET", "/pickups/{id}", path_params={"id": pid}, claims={"cognito:groups": groups})
            return invoke(pickups_handler.handler, ev)[1]

        self.assertEqual(leaks(get("viewers")), [])
        self.assertEqual(leaks(get("")), [])
        self.assertIn(HOUSEHOLD_ADDRESS, json.dumps(get("[admins viewers]")))

    def test_list_pickups_redacts_for_non_admins(self):
        self.new_pickup()
        ev = api_event("GET", "/pickups", query={"status": "requested"}, claims={"cognito:groups": "viewers"})
        status, body = invoke(pickups_handler.handler, ev)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["pickups"]), 1)
        self.assertEqual(leaks(body), [])


class OfferFlowTests(AwsTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.new_pickup()

    def offer(self, recycler_id="R001"):
        return invoke(voice_handler.handler, api_event(
            "POST", "/voice/offer", {"pickup_id": self.pid, "recycler_id": recycler_id}))

    def press(self, digit):
        """The recycler's keypad press, delivered the way Twilio delivers it (signed form POST)."""
        return invoke(voice_handler.handler, twilio_event(
            "/voice/response", {"Digits": digit, "CallSid": "CA1"}, query={"pickup_id": self.pid}))

    def stored(self):
        return self.repo.get_pickup(self.pid)

    def test_offer_places_a_real_call_without_the_address(self):
        status, body = self.offer()
        self.assertEqual(status, 200, body)
        self.assertEqual((body["status"], body["recycler_id"]), ("offered", "R001"))

        (call,) = self.server.calls()
        self.assertEqual(call["To"], RECYCLER_PHONE)
        self.assertIn("Press 1 to accept", call["Twiml"])
        self.assertIn(f"/prod/voice/response?pickup_id={self.pid}", call["Twiml"])
        self.assertEqual(leaks(call), [])
        self.assertEqual(self.server.messages(), [])  # nobody has been messaged yet
        self.assertEqual(self.stored()["status"], "offered")
        self.assertEqual(self.stored()["recycler_id"], "R001")
        self.assertTrue(self.server.requests[0]["headers"]["Authorization"].startswith("Basic "))

    def test_address_goes_to_the_recycler_only_after_digit_1(self):
        self.offer()
        status, body = self.press("1")
        self.assertEqual(status, 200)
        self.assertIn("Pickup confirmed", body)  # TwiML read back to the recycler
        self.assertEqual(self.stored()["status"], "confirmed")

        by_recipient = {m["To"]: m["Body"] for m in self.server.messages()}
        self.assertEqual(set(by_recipient), {f"whatsapp:{RECYCLER_PHONE}", USER_ID})  # both sides told
        self.assertIn(HOUSEHOLD_ADDRESS, by_recipient[f"whatsapp:{RECYCLER_PHONE}"])
        self.assertEqual(leaks(by_recipient[USER_ID]), [])
        self.assertIn("Sharma Kabadi Works", by_recipient[USER_ID])

    def test_address_is_never_sent_after_digit_2(self):
        self.offer()
        self.press("2")
        self.assertEqual(self.stored()["status"], "declined")
        self.assertEqual(leaks(self.server.requests), [])  # nothing sent anywhere contains it
        self.assertEqual([m["To"] for m in self.server.messages()], [USER_ID])

    def test_sns_events_follow_the_lifecycle_and_never_carry_the_address(self):
        self.offer()
        self.press("1")
        events = self.sns_events()
        self.assertEqual([(e["status"], e["previous_status"]) for e in events],
                         [("requested", None), ("offered", "requested"), ("confirmed", "offered")])
        self.assertEqual(leaks(events), [])

    def test_unsigned_or_forged_keypad_callback_cannot_confirm(self):
        self.offer()
        status, body = invoke(voice_handler.handler, twilio_event(
            "/voice/response", {"Digits": "1"}, query={"pickup_id": self.pid}, token=None))
        self.assertEqual((status, body["error"]), (403, "invalid_signature"))
        status, _ = invoke(voice_handler.handler, twilio_event(
            "/voice/response", {"Digits": "1"}, query={"pickup_id": self.pid}, token="wrong-token"))
        self.assertEqual(status, 403)
        self.assertEqual(self.stored()["status"], "offered")
        self.assertEqual(self.server.messages(), [])

    def test_failed_call_rolls_back_and_leaks_nothing(self):
        self.server.responder = lambda path, body: (400, {"message": "carrier rejected the call"})
        status, body = self.offer()
        self.assertEqual((status, body["error"]), (502, "voice_provider_error"))
        self.assertEqual(self.stored()["status"], "requested")
        self.assertNotIn("recycler_id", self.stored())
        self.assertEqual(leaks(body), [])
        self.server.responder = type(self.server)._default  # recycler can be offered again
        self.assertEqual(self.offer()[0], 200)

    def test_service_refuses_a_payload_that_would_leak(self):
        # Defence in depth: if the view builder were ever broken, the guard stops the call.
        import ecolink.core.pickup_service as ps

        original = ps.recycler_view
        ps.recycler_view = lambda p, *a, **k: {**original(p, *a, **k), "address_text": p["address_text"]}
        self.addCleanup(setattr, ps, "recycler_view", original)
        status, body = self.offer()
        self.assertEqual(status, 500)
        self.assertEqual(self.server.calls(), [])
        self.assertEqual(leaks(body), [])
        self.assertEqual(self.stored()["status"], "requested")

    def test_patch_cannot_bypass_the_voice_offer(self):
        for target in ("offered", "confirmed"):
            status, body = invoke(pickups_handler.handler, api_event(
                "PATCH", "/pickups/{id}", {"status": target}, path_params={"id": self.pid}))
            self.assertEqual(status, 409, body)
        self.assertEqual(self.stored()["status"], "requested")


if __name__ == "__main__":
    unittest.main()
