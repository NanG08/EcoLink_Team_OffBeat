"""Dashboard backend: /auth/login (emulated Cognito), enriched pickup views, admin-only writes."""

from __future__ import annotations

import json
import os
from unittest import mock

import boto3

from ecolink.handlers import auth, dashboard, pickups, voice
from tests.support import (HOUSEHOLD_ADDRESS, REGION, USER_ID, AwsTestCase, api_event, invoke,
                           twilio_event)

PASSWORD = "Str0ng-Passw0rd!"


def claims(*groups):
    return {"cognito:groups": " ".join(groups)}


class LoginTests(AwsTestCase):
    seed_data = False

    def setUp(self):
        super().setUp()
        self.idp = boto3.client("cognito-idp", region_name=REGION)
        pool = self.idp.create_user_pool(PoolName="test", UsernameAttributes=["email"])["UserPool"]["Id"]
        self.client_id = self.idp.create_user_pool_client(
            UserPoolId=pool, ClientName="dash", ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        )["UserPoolClient"]["ClientId"]
        self.pool = pool
        patcher = mock.patch.dict(os.environ, {"COGNITO_CLIENT_ID": self.client_id})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.idp.create_group(UserPoolId=pool, GroupName="admins")

    def make_user(self, email, *, group=None, temporary=False):
        self.idp.admin_create_user(UserPoolId=self.pool, Username=email, TemporaryPassword=PASSWORD,
                                   UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
                                   MessageAction="SUPPRESS")
        if not temporary:
            self.idp.admin_set_user_password(UserPoolId=self.pool, Username=email, Password=PASSWORD, Permanent=True)
        if group:
            self.idp.admin_add_user_to_group(UserPoolId=self.pool, Username=email, GroupName=group)

    def login(self, **body):
        return invoke(auth.handler, api_event("POST", "/auth/login", body))

    def test_successful_login_returns_the_shape_the_dashboard_expects(self):
        self.make_user("ops@example.com")
        status, body = self.login(email="ops@example.com", password=PASSWORD)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["user"]["email"], "ops@example.com")
        self.assertEqual(body["group"], "viewers")
        self.assertEqual(body["id_token"].count("."), 2)  # a JWT
        self.assertGreater(body["expires_in"], 0)

    def test_admin_group_membership_is_reported(self):
        self.make_user("boss@example.com", group="admins")
        status, body = self.login(email="boss@example.com", password=PASSWORD)
        self.assertEqual((status, body["group"]), (200, "admins"))

    def test_wrong_password_and_unknown_user_are_401_without_revealing_which(self):
        self.make_user("ops@example.com")
        for email, password in (("ops@example.com", "wrong-Passw0rd!"), ("nobody@example.com", PASSWORD)):
            status, body = self.login(email=email, password=password)
            self.assertEqual((status, body["error"]), (401, "invalid_credentials"))
            self.assertEqual(body["detail"], "Incorrect email or password")

    def test_first_login_with_a_temporary_password_requires_a_new_one(self):
        self.make_user("new@example.com", temporary=True)
        status, body = self.login(email="new@example.com", password=PASSWORD)
        self.assertEqual((status, body["error"]), (409, "new_password_required"))
        status, body = self.login(email="new@example.com", password=PASSWORD, new_password="Another-Str0ng-One!")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.login(email="new@example.com", password="Another-Str0ng-One!")[0], 200)

    def test_missing_fields_are_400(self):
        self.assertEqual(self.login(email="a@example.com")[0], 400)
        self.assertEqual(self.login(password=PASSWORD)[0], 400)


class DashboardViewTests(AwsTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.new_pickup(items=[{"label": "paper", "weight_kg": 2}, {"label": "plastic", "weight_kg": 1}])

    def get(self, *groups, pid=None):
        return invoke(pickups.handler, api_event(
            "GET", "/pickups/{id}", path_params={"id": pid or self.pid}, claims=claims(*groups)))

    def confirm(self):
        invoke(voice.handler, api_event("POST", "/voice/offer", {"pickup_id": self.pid, "recycler_id": "R001"}))
        invoke(voice.handler, twilio_event("/voice/response", {"Digits": "1"}, query={"pickup_id": self.pid}))

    def test_admin_view_has_everything_the_console_renders(self):
        self.confirm()
        status, view = self.get("admins")
        self.assertEqual(status, 200)
        self.assertEqual(view["recycler_name"], "Sharma Kabadi Works")
        self.assertEqual(view["estimated_value"], {"low": 28, "high": 46, "currency": "INR"})  # paper 2kg + plastic 1kg
        self.assertEqual([e["detail"] for e in view["action_log"]],
                         ["Status updated to REQUESTED", "Status updated to OFFERED", "Status updated to CONFIRMED"])
        self.assertEqual((view["address_text"], view["user_id"]), (HOUSEHOLD_ADDRESS, USER_ID))
        self.assertEqual((float(view["lat"]), float(view["lng"])), (12.9716, 77.5946))

    def test_viewer_gets_placeholders_and_no_location_before_confirmation(self):
        status, view = self.get("viewers")
        self.assertEqual(status, 200)
        self.assertEqual(view["address_text"], "[Hidden until confirmed]")
        self.assertEqual(view["user_id"], "User (protected)")
        self.assertNotIn("lat", view)
        self.assertNotIn("lng", view)
        self.assertNotIn("Rosewood", json.dumps(view))
        self.assertNotIn("919800000001", json.dumps(view))

    def test_viewer_sees_address_and_location_once_confirmed(self):
        self.confirm()
        _, view = self.get("viewers")
        self.assertEqual(view["address_text"], HOUSEHOLD_ADDRESS)
        self.assertIn("lat", view)

    def test_list_view_applies_the_same_rules(self):
        status, body = invoke(pickups.handler, api_event("GET", "/pickups", claims=claims("viewers")))
        self.assertEqual(status, 200)
        (view,) = body["pickups"]
        self.assertEqual(view["address_text"], "[Hidden until confirmed]")
        self.assertNotIn("lat", view)

    def test_recyclers_listing_hides_phones_from_viewers(self):
        def listing(*groups):
            return invoke(dashboard.handler, api_event("GET", "/dashboard/recyclers", claims=claims(*groups)))

        status, rows = listing("viewers")
        self.assertEqual((status, len(rows)), (200, 8))  # includes the unavailable R008
        self.assertTrue(all("phone" not in r for r in rows))
        self.assertEqual(set(rows[0]), {"recycler_id", "name", "lat", "lng", "accepts", "available", "lang"})
        self.assertFalse(next(r for r in rows if r["recycler_id"] == "R008")["available"])
        self.assertTrue(all("phone" in r for r in listing("admins")[1]))


class DashboardWriteTests(AwsTestCase):
    def patch(self, pid, status, caller_claims=None):
        return invoke(pickups.handler, api_event(
            "PATCH", "/dashboard/pickups/{id}", {"status": status}, path_params={"id": pid}, claims=caller_claims))

    def test_only_admins_can_change_status_from_the_dashboard(self):
        pid = self.new_pickup()
        status, body = self.patch(pid, "cancelled", claims("viewers"))
        self.assertEqual((status, body["error"]), (403, "forbidden"))
        self.assertEqual(self.repo.get_pickup(pid)["status"], "requested")
        self.assertEqual(self.patch(pid, "cancelled", claims("admins"))[0], 200)
        self.assertEqual(self.repo.get_pickup(pid)["status"], "cancelled")

    def test_api_key_callers_such_as_the_agent_are_unaffected(self):
        pid = self.new_pickup()
        status, body = self.patch(pid, "cancelled", None)  # no Cognito claims
        self.assertEqual((status, body["status"]), (200, "cancelled"))

    def test_admin_overrides_still_obey_the_state_machine_and_voice_rules(self):
        pid = self.new_pickup()
        self.assertEqual(self.patch(pid, "offered", claims("admins"))[0], 409)
        self.assertEqual(self.patch(pid, "completed", claims("admins"))[0], 409)
