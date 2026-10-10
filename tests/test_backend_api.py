"""Handler-level tests: pickups, voice, pricing, agent proxy, uploads, webhook, authorizer,
repository, Twilio adapter and the seed/reset scripts. See tests/support.py for what is real."""

from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from unittest import mock

import boto3

from ecolink import deps
from ecolink.core import classifier
from ecolink.core.http import ApiError
from ecolink.core.repository import ConflictError
from ecolink.handlers import authorizer, messaging_webhook, pickups, pricing, proxy, uploads, voice
from ecolink.providers import twilio
from ecolink.providers.base import ProviderError, WebhookRequest
from tests.support import (API_KEY, HOUSEHOLD_ADDRESS, RECYCLER_PHONE, REGION, SLOT, TWILIO_TOKEN,
                           USER_ID, AwsTestCase, api_event, invoke, twilio_event)


class PickupApiTests(AwsTestCase):
    def create(self, **overrides):
        body = {"user_id": USER_ID, "items": [{"label": "paper", "weight_kg": 2}],
                "address_text": HOUSEHOLD_ADDRESS, "slot": SLOT, **overrides}
        return invoke(pickups.handler, api_event("POST", "/pickups", body))

    def patch(self, pid, status):
        return invoke(pickups.handler, api_event("PATCH", "/pickups/{id}", {"status": status}, path_params={"id": pid}))

    def test_create_persists_to_dynamodb_and_returns_201(self):
        status, body = self.create()
        self.assertEqual(status, 201)
        self.assertTrue(body["pickup_id"].startswith("pk_"))
        self.assertEqual(body["status"], "requested")
        self.assertNotIn(HOUSEHOLD_ADDRESS, json.dumps(body))
        stored = self.ddb.Table("EcoLink-pickups").get_item(Key={"pickup_id": body["pickup_id"]})["Item"]
        self.assertEqual(stored["address_text"], HOUSEHOLD_ADDRESS)
        self.assertEqual(stored["items"], [{"label": "paper", "weight_kg": 2}])

    def test_hazardous_items_are_blocked_server_side(self):
        for label in ("battery", "ewaste"):
            status, body = self.create(items=[{"label": label}])
            self.assertEqual((status, body["error"]), (422, "hazardous_items_blocked"))
            self.assertIn("drop-off", body["detail"])
        self.assertEqual(self.ddb.Table("EcoLink-pickups").scan()["Items"], [])

    def test_validation_errors_are_clear(self):
        for override in ({"items": []}, {"items": [{"label": "gold"}]}, {"items": [{"label": "paper", "weight_kg": -1}]},
                         {"address_text": ""}, {"slot": "tomorrow morning"}, {"user_id": None}):
            with self.subTest(override=override):
                status, body = self.create(**override)
                self.assertEqual((status, body["error"]), (400, "invalid_request"))
                self.assertTrue(body["detail"])
        ev = api_event("POST", "/pickups")
        ev["body"] = "{nope"
        self.assertEqual(invoke(pickups.handler, ev)[0], 400)

    def test_unknown_item_fields_are_not_stored(self):
        _, body = self.create(items=[{"label": "paper", "weight_kg": 1, "image_url": "https://x"}])
        self.assertEqual(self.repo.get_pickup(body["pickup_id"])["items"], [{"label": "paper", "weight_kg": 1}])

    def test_patch_enforces_the_state_machine(self):
        pid = self.create()[1]["pickup_id"]
        self.assertEqual(self.patch(pid, "cancelled")[0], 200)
        status, body = self.patch(pid, "completed")  # cancelled is terminal
        self.assertEqual((status, body["error"]), (409, "invalid_status_transition"))
        self.assertEqual(self.patch(pid, "cancelled")[0], 200)  # idempotent
        self.assertEqual(self.patch(pid, "shipped")[0], 400)
        self.assertEqual(self.patch("pk_missing", "cancelled")[0], 404)

    def test_full_lifecycle_to_completed_records_history(self):
        pid = self.new_pickup()
        invoke(voice.handler, api_event("POST", "/voice/offer", {"pickup_id": pid, "recycler_id": "R001"}))
        invoke(voice.handler, twilio_event("/voice/response", {"Digits": "1"}, query={"pickup_id": pid}))
        self.assertEqual(self.patch(pid, "completed")[1]["status"], "completed")
        history = [h["status"] for h in self.repo.get_pickup(pid)["status_history"]]
        self.assertEqual(history, ["requested", "offered", "confirmed", "completed"])

    def test_list_filters_by_status_and_user(self):
        a, b = self.new_pickup(), self.new_pickup()
        self.patch(b, "cancelled")

        def get(**q):
            return invoke(pickups.handler, api_event("GET", "/pickups", query=q, claims={"cognito:groups": "admins"}))

        self.assertEqual([p["pickup_id"] for p in get(status="requested")[1]["pickups"]], [a])
        self.assertEqual(len(get()[1]["pickups"]), 2)
        self.assertEqual(len(get(user_id=USER_ID, status="cancelled")[1]["pickups"]), 1)
        self.assertEqual(get(status="bogus")[0], 400)


class VoiceApiTests(AwsTestCase):
    def offer(self, pid, rid="R001"):
        return invoke(voice.handler, api_event("POST", "/voice/offer", {"pickup_id": pid, "recycler_id": rid}))

    def press(self, pid, digit):
        return invoke(voice.handler, twilio_event("/voice/response", {"Digits": digit}, query={"pickup_id": pid}))

    def test_offer_error_cases(self):
        pid = self.new_pickup(items=[{"label": "paper"}, {"label": "metal"}])
        self.assertEqual(self.offer(pid, "R002")[1]["error"], "recycler_cannot_accept")  # R002 has no metal
        self.assertEqual(self.offer(pid, "R008")[1]["error"], "recycler_unavailable")  # seeded closed
        self.assertEqual(self.offer(pid, "R999")[0], 404)
        self.assertEqual(self.offer("pk_missing")[0], 404)
        self.assertEqual(self.server.calls(), [])
        self.assertEqual(self.repo.get_pickup(pid)["status"], "requested")

    def test_second_offer_on_an_offered_pickup_is_rejected(self):
        pid = self.new_pickup()
        self.assertEqual(self.offer(pid)[0], 200)
        status, body = self.offer(pid, "R004")
        self.assertEqual((status, body["error"]), (409, "invalid_status_transition"))
        self.assertEqual(len(self.server.calls()), 1)

    def test_invalid_digit_and_missing_fields(self):
        pid = self.new_pickup()
        self.offer(pid)
        status, body = self.press(pid, "5")
        self.assertEqual((status, body["error"]), (400, "invalid_digit"))
        status, _ = invoke(voice.handler, twilio_event("/voice/response", {"CallSid": "CA1"}, query={"pickup_id": pid}))
        self.assertEqual(status, 400)
        self.assertEqual(self.repo.get_pickup(pid)["status"], "offered")

    def test_duplicate_digit_is_idempotent_and_messages_once(self):
        pid = self.new_pickup()
        self.offer(pid)
        self.press(pid, "1")
        sent = len(self.server.messages())
        self.assertEqual(self.press(pid, "1")[0], 200)
        self.assertEqual(len(self.server.messages()), sent)

    def test_conflicting_late_digit_is_409(self):
        pid = self.new_pickup()
        self.offer(pid)
        self.press(pid, "1")
        status, body = self.press(pid, "2")
        self.assertEqual((status, body["error"]), (409, "invalid_status_transition"))

    def test_declined_pickup_can_be_offered_to_another_recycler(self):
        pid = self.new_pickup()
        self.offer(pid)
        self.press(pid, "2")
        self.assertEqual(self.offer(pid, "R004")[0], 200)
        stored = self.repo.get_pickup(pid)
        self.assertEqual((stored["status"], stored["recycler_id"]), ("offered", "R004"))
        self.assertEqual(self.server.calls()[-1]["To"], RECYCLER_PHONE)

    def json_press(self, pid, digit, key=API_KEY):
        ev = api_event("POST", "/voice/response", {"pickup_id": pid, "digit": digit})
        if key:
            ev["headers"] = {"Authorization": f"Bearer {key}"}
        return invoke(voice.handler, ev)

    def test_json_contract_callback_with_api_key_updates_status(self):
        pid = self.new_pickup()
        self.offer(pid)
        status, body = self.json_press(pid, "1")
        self.assertEqual((status, body), (200, {"pickup_id": pid, "status": "confirmed"}))
        self.assertEqual(self.repo.get_pickup(pid)["status"], "confirmed")
        self.assertEqual(len(self.server.messages()), 2)  # both sides notified, same as a keypad press

    def test_json_contract_callback_needs_the_api_key(self):
        pid = self.new_pickup()
        self.offer(pid)
        self.assertEqual(self.json_press(pid, "1", key="wrong")[0], 401)
        status, body = self.json_press(pid, "1", key=None)
        self.assertEqual((status, body["error"]), (403, "invalid_signature"))
        self.assertEqual(self.json_press(pid, "7")[1]["error"], "invalid_digit")
        self.assertEqual(self.repo.get_pickup(pid)["status"], "offered")

    def test_distance_is_read_out_when_the_user_has_a_location(self):
        pid = self.new_pickup()
        self.offer(pid)
        self.assertIn("kilometres", self.server.calls()[0]["Twiml"])


class PricingApiTests(AwsTestCase):
    def price(self, **q):
        return invoke(pricing.handler, api_event("GET", "/price", query=q))

    def quote(self, **body):
        return invoke(pricing.handler, api_event("POST", "/ewaste/quote", body))

    def test_scrap_price_from_rate_table(self):
        status, body = self.price(label="paper", weight_kg="3")
        self.assertEqual(status, 200)
        self.assertEqual((body["low"], body["high"], body["currency"]), (30, 42, "INR"))
        self.assertRegex(body["updated"], r"^\d{4}-\d{2}-\d{2}$")

    def test_price_errors(self):
        self.assertEqual(self.price(label="gold", weight_kg="1")[0], 400)
        self.assertEqual(self.price(label="paper", weight_kg="0")[0], 400)
        self.assertEqual(self.price(label="paper")[0], 400)
        self.assertEqual(self.price(label="battery", weight_kg="1")[1]["error"], "hazardous_label")
        self.ddb.Table("EcoLink-rate-table").delete_item(Key={"kind": "scrap", "item": "glass"})
        self.assertEqual(self.price(label="glass", weight_kg="1")[0], 404)

    def test_working_laptop_is_sell(self):
        status, body = self.quote(item_type="Laptop", age_years=2, condition="working")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"low": 10500, "high": 24500, "recommendation": "sell"})  # 0.7 age factor

    def test_old_broken_laptop_is_dropoff(self):
        _, body = self.quote(item_type="laptop", age_years=9, condition="broken")
        self.assertEqual(body["recommendation"], "dropoff")
        self.assertLess(body["high"], 500)

    def test_battery_is_always_dropoff(self):
        _, body = self.quote(item_type="Lithium battery", age_years=0, condition="working")
        self.assertEqual(body, {"low": 0, "high": 0, "recommendation": "dropoff"})

    def test_aliases_fallback_and_bad_input(self):
        self.assertEqual(self.quote(item_type="smartphone", age_years=1, condition="good")[0], 200)
        self.assertEqual(self.quote(item_type="toaster", age_years=1, condition="working")[0], 200)  # "other" row
        status, body = self.quote(item_type="laptop", age_years=1, condition="haunted")
        self.assertEqual(status, 400)
        self.assertIn("working", body["detail"])
        self.assertEqual(self.quote(item_type="laptop", age_years=-1, condition="working")[0], 400)


class AgentProxyTests(AwsTestCase):
    def message(self, **body):
        return invoke(proxy.handler, api_event("POST", "/agent/message", body))

    def test_message_is_forwarded_over_http_to_the_agent(self):
        status, body = self.message(user_id=USER_ID, text="hello", lang="en")
        self.assertEqual(status, 200)
        self.assertEqual(body["reply_text"], "Namaste! Regular waste or e-waste?")
        forwarded = json.loads(self.server.requests[-1]["body"])
        self.assertEqual(forwarded, {"user_id": USER_ID, "text": "hello", "lang": "en"})

    def test_validation_and_upstream_failures(self):
        self.assertEqual(self.message(user_id=USER_ID)[0], 400)
        self.server.responder = lambda p, b: (500, {"detail": "model unavailable"})
        status, body = self.message(user_id=USER_ID, text="hi")
        self.assertEqual((status, body["error"]), (502, "upstream_error"))
        self.server.responder = lambda p, b: (400, {"detail": "bad text"})
        self.assertEqual(self.message(user_id=USER_ID, text="hi")[1]["error"], "upstream_rejected")

    def test_not_configured_is_an_explicit_503_not_a_fake_reply(self):
        del os.environ["AGENT_URL"]
        status, body = self.message(user_id=USER_ID, text="hi")
        self.assertEqual((status, body["error"]), (503, "not_configured"))


class ClassifierTests(AwsTestCase):
    """The Bedrock call itself needs real AWS; scripts/smoke_test.py covers it on a deployed stack."""

    def test_parse_model_output_normalises_to_the_contract(self):
        text = 'Sure! ```json\n{"items":[{"label":"Plastic","confidence":0.9},{"label":"battery","confidence":2},' \
               '{"label":"plastic","confidence":0.4},{"label":"unicorn","confidence":1}]}```'
        self.assertEqual(classifier.parse_model_output(text), [
            {"label": "battery", "confidence": 1.0, "hazardous": True},
            {"label": "plastic", "confidence": 0.9, "hazardous": False},
        ])
        self.assertEqual(classifier.parse_model_output('{"items":[]}'), [])
        with self.assertRaises(ApiError):
            classifier.parse_model_output("I cannot help with that")

    def test_image_format_sniffing(self):
        self.assertEqual(classifier.image_format(b"\xff\xd8\xff\xe0abc"), "jpeg")
        self.assertEqual(classifier.image_format(b"\x89PNG\r\n\x1a\n...."), "png")
        self.assertEqual(classifier.image_format(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        with self.assertRaises(ApiError):
            classifier.image_format(b"<html>")

    def test_private_loopback_and_metadata_addresses_are_never_fetched(self):
        for url in ("http://example.com/a.jpg", "file:///etc/passwd", "ftp://example.com/a.jpg",
                    "https://169.254.169.254/latest/meta-data", "https://127.0.0.1/a.jpg",
                    "https://10.0.0.5/a.jpg", "https://192.168.1.1/a.jpg", "https://[::1]/a.jpg",
                    "https://localhost/a.jpg", "https://[fe80::1]/a.jpg"):
            with self.subTest(url=url), self.assertRaises(ApiError) as ctx:
                classifier.fetch_image(url)
            self.assertEqual(ctx.exception.status, 400)

    def test_any_public_https_host_is_accepted(self):
        classifier.assert_public_https("https://8.8.8.8/photo.jpg")  # IP literal: no DNS needed
        classifier.assert_public_https("https://[2001:4860:4860::8888]/photo.jpg")

    def test_classify_sends_image_and_prompt_to_bedrock_converse(self):
        calls = []

        class Bedrock:  # records the request; returns what Converse would
            def converse(self, **kw):
                calls.append(kw)
                return {"output": {"message": {"content": [{"text": '{"items":[{"label":"metal","confidence":0.8}]}'}]}}}

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
        with mock.patch.dict(os.environ, {"CLASSIFY_MODEL_ID": "apac.amazon.nova-pro-v1:0"}), \
             mock.patch.object(classifier, "fetch_image", return_value=png):
            result = classifier.classify("https://b.s3.amazonaws.com/x.png", client=Bedrock())
        self.assertEqual(result, {"items": [{"label": "metal", "confidence": 0.8, "hazardous": False}], "source": "bedrock"})
        content = calls[0]["messages"][0]["content"]
        self.assertEqual(calls[0]["modelId"], "apac.amazon.nova-pro-v1:0")
        self.assertEqual(content[0]["image"], {"format": "png", "source": {"bytes": png}})

    def test_classify_route_requires_model_configuration(self):
        status, body = invoke(proxy.handler, api_event("POST", "/classify", {"image_url": "https://b.s3.amazonaws.com/x.png"}))
        self.assertEqual((status, body["error"]), (503, "not_configured"))
        self.assertEqual(invoke(proxy.handler, api_event("POST", "/classify", {}))[0], 400)


class UploadTests(AwsTestCase):
    def test_presigned_post_is_scoped_to_one_key_type_and_size(self):
        status, body = invoke(uploads.handler, api_event(
            "POST", "/uploads/presign", {"user_id": USER_ID, "content_type": "image/jpeg"}))
        self.assertEqual(status, 200)
        self.assertTrue(body["key"].startswith("images/") and body["key"].endswith(".jpg"))
        self.assertNotIn("919800000001", body["key"])  # no phone number in S3 keys
        policy = json.loads(base64.b64decode(body["fields"]["policy"]))
        self.assertIn(["content-length-range", 1, 5 * 1024 * 1024], policy["conditions"])
        self.assertIn({"bucket": "ecolink-test-images"}, policy["conditions"])
        self.assertIn({"Content-Type": "image/jpeg"}, policy["conditions"])
        self.assertIn("X-Amz-Signature=", body["image_url"])
        self.assertEqual(invoke(uploads.handler, api_event(
            "POST", "/uploads/presign", {"user_id": USER_ID, "content_type": "text/html"}))[0], 400)

    def test_put_image_stores_in_the_private_bucket_and_url_is_signed(self):
        store = deps.get_image_store()
        key = store.put_image(USER_ID, b"\xff\xd8\xff data", "image/jpeg")
        obj = boto3.client("s3", region_name=REGION).get_object(Bucket="ecolink-test-images", Key=key)
        self.assertEqual(obj["Body"].read(), b"\xff\xd8\xff data")
        self.assertIn("Signature=", store.presigned_get(key))


class WebhookTests(AwsTestCase):
    def inbound(self, **form):
        form = {"From": USER_ID, "Body": "hello", "NumMedia": "0", "MessageSid": "SM1", **form}
        return invoke(messaging_webhook.handler, twilio_event("/webhooks/messaging", form))

    def test_inbound_goes_to_agent_and_reply_is_sent_back_via_twilio(self):
        status, body = self.inbound()
        self.assertEqual((status, body), (200, "<Response/>"))
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        # The seeded user has a saved location, which the agent needs for find_recyclers.
        self.assertEqual(forwarded, {"user_id": USER_ID, "lang": "en",
                                     "text": "[USER_LOCATION: lat=12.9716 lng=77.5946]" + chr(10) + "hello"})
        (msg,) = self.server.messages()
        self.assertEqual((msg["To"], msg["From"], msg["Body"]),
                         (USER_ID, "whatsapp:+14155238886", "Namaste! Regular waste or e-waste?"))

    def test_new_users_are_recorded_and_whatsapp_location_is_saved(self):
        self.inbound(From="whatsapp:+911111111111", Latitude="12.95", Longitude="77.60", Body="")
        user = self.repo.get_user("whatsapp:+911111111111")
        self.assertEqual(float(user["lat"]), 12.95)
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertEqual(forwarded["text"], "[USER_LOCATION: lat=12.95 lng=77.6]" + chr(10) + "(location shared)")

    def test_no_location_tag_when_the_location_is_unknown(self):
        self.inbound(From="whatsapp:+912222222222", Body="hi")
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertEqual(forwarded["text"], "hi")

    def test_hindi_is_detected(self):
        self.inbound(Body="नमस्ते")
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertEqual(forwarded["lang"], "hi")

    def test_agent_failure_still_replies_to_the_user(self):
        self.server.responder = lambda path, body: (
            (500, {"detail": "boom"}) if path == "/agent/message" else self.server._default(path, body))
        status, _ = self.inbound()
        self.assertEqual(status, 200)
        self.assertIn("Sorry", self.server.messages()[0]["Body"])

    def test_unsigned_and_forged_webhooks_are_rejected(self):
        for token in (None, "wrong-token"):
            status, body = invoke(messaging_webhook.handler, twilio_event(
                "/webhooks/messaging", {"From": USER_ID, "Body": "hi"}, token=token))
            self.assertEqual((status, body["error"]), (403, "invalid_signature"))
        self.assertEqual(self.server.requests, [])  # nothing forwarded, nothing sent

    def test_media_from_a_non_twilio_host_is_dropped_and_credentials_are_never_sent(self):
        self.inbound(NumMedia="1", MediaUrl0="https://evil.example.com/p.jpg", MediaContentType0="image/jpeg", Body="")
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertNotIn("image_url", forwarded)

    MEDIA_PATH = "/Accounts/AC/Messages/MM1/Media/ME1"

    def test_photo_is_copied_to_private_s3_and_deleted_from_twilio(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"j" * 64
        default = self.server._default
        self.server.responder = lambda path, body: (
            (200, jpeg) if path == self.MEDIA_PATH else default(path, body))
        self.inbound(NumMedia="1", MediaUrl0=self.server.url + self.MEDIA_PATH,
                     MediaContentType0="image/jpeg", Body="")

        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertIn("ecolink-test-images", forwarded["image_url"])
        self.assertIn("Signature=", forwarded["image_url"])
        s3 = boto3.client("s3", region_name=REGION)
        (obj,) = s3.list_objects_v2(Bucket="ecolink-test-images")["Contents"]
        self.assertTrue(obj["Key"].startswith("images/"))
        self.assertEqual(s3.get_object(Bucket="ecolink-test-images", Key=obj["Key"])["Body"].read(), jpeg)

        download = next(r for r in self.server.requests if r["method"] == "GET")
        self.assertTrue(download["headers"]["Authorization"].startswith("Basic "))
        deletion = next(r for r in self.server.requests if r["method"] == "DELETE")
        self.assertEqual(deletion["path"], self.MEDIA_PATH + ".json")  # Twilio's copy is removed

    def test_non_image_media_is_not_stored(self):
        self.server.responder = lambda path, body: (
            (200, b"%PDF-1.4", {"Content-Type": "application/pdf"}) if path == self.MEDIA_PATH
            else self.server._default(path, body))
        self.inbound(NumMedia="1", MediaUrl0=self.server.url + self.MEDIA_PATH,
                     MediaContentType0="application/pdf", Body="")
        self.assertNotIn("Contents", boto3.client("s3", region_name=REGION).list_objects_v2(Bucket="ecolink-test-images"))
        forwarded = json.loads(next(r for r in self.server.requests if r["path"] == "/agent/message")["body"])
        self.assertNotIn("image_url", forwarded)


class AuthorizerTests(AwsTestCase):
    ARN = "arn:aws:execute-api:ap-south-1:123456789012:abc123/prod/POST/pickups"

    def auth(self, token):
        return authorizer.handler({"authorizationToken": token, "methodArn": self.ARN}, None)

    def test_valid_token_from_secrets_manager_allows_the_stage(self):
        stmt = self.auth(f"Bearer {API_KEY}")["policyDocument"]["Statement"][0]
        self.assertEqual(stmt["Effect"], "Allow")
        self.assertEqual(stmt["Resource"], "arn:aws:execute-api:ap-south-1:123456789012:abc123/prod/*/*")

    def test_bad_tokens_are_unauthorized(self):
        for token in ("Bearer wrong", API_KEY, "Bearer ", "", f"Basic {API_KEY}"):
            with self.subTest(token=token), self.assertRaises(Exception) as ctx:
                self.auth(token)
            self.assertEqual(str(ctx.exception), "Unauthorized")

    def test_missing_secret_denies(self):
        with mock.patch.dict(os.environ, {"API_KEY_SECRET_NAME": "does/not/exist"}), self.assertRaises(Exception):
            self.auth(f"Bearer {API_KEY}")


class RepositoryTests(AwsTestCase):
    def test_transition_is_conditional_on_current_status(self):
        pid = self.new_pickup()
        self.repo.transition_pickup(pid, "requested", "cancelled")
        with self.assertRaises(ConflictError):
            self.repo.transition_pickup(pid, "requested", "offered")  # stale expectation
        self.assertEqual(self.repo.get_pickup(pid)["status"], "cancelled")

    def test_transition_can_set_and_remove_fields(self):
        pid = self.new_pickup()
        self.repo.transition_pickup(pid, "requested", "offered", set_fields={"recycler_id": "R001"})
        self.assertEqual(self.repo.get_pickup(pid)["recycler_id"], "R001")
        self.repo.transition_pickup(pid, "offered", "requested", remove_fields=("recycler_id",))
        self.assertNotIn("recycler_id", self.repo.get_pickup(pid))

    def test_ensure_user_does_not_overwrite_and_location_updates(self):
        self.repo.ensure_user(USER_ID, "hi")
        self.assertEqual(self.repo.get_user(USER_ID)["lang"], "en")  # seeded record untouched
        self.repo.set_user_location(USER_ID, 12.5, 77.5)
        self.assertEqual(float(self.repo.get_user(USER_ID)["lat"]), 12.5)


class TwilioAdapterTests(AwsTestCase):
    def test_signature_matches_official_twilio_library(self):
        # Expected value produced by twilio.request_validator.RequestValidator("12345").
        sig = twilio.compute_signature(
            "12345", "https://mycompany.com/myapp.php?foo=1&bar=2",
            [("CallSid", "CA1234567890ABCDE"), ("Caller", "+12349013030"), ("Digits", "1234"),
             ("From", "+12349013030"), ("To", "+18005551212")])
        self.assertEqual(sig, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")

    def test_verify_accepts_valid_and_rejects_tampered(self):
        url, body = "https://api.test/prod/x", "From=a&Body=hi"
        good = twilio.compute_signature(TWILIO_TOKEN, url, [("From", "a"), ("Body", "hi")])
        provider = twilio.TwilioMessagingProvider()
        self.assertTrue(provider.verify_request(WebhookRequest("POST", url, {"x-twilio-signature": good}, {}, body)))
        self.assertFalse(provider.verify_request(WebhookRequest("POST", url, {"x-twilio-signature": good}, {}, body + "x")))
        self.assertFalse(provider.verify_request(WebhookRequest("POST", url, {}, {}, body)))

    def test_send_text_posts_basic_auth_form_to_twilio(self):
        sid = twilio.TwilioMessagingProvider().send_text("+919812345678", "hi")  # bare phone -> whatsapp:
        self.assertTrue(sid.startswith("SM"))
        req = self.server.requests[-1]
        self.assertEqual(req["path"], "/Accounts/AC" + "0" * 32 + "/Messages.json")
        self.assertEqual(self.server.messages()[-1]["To"], "whatsapp:+919812345678")
        self.assertEqual(base64.b64decode(req["headers"]["Authorization"].split()[1]).decode(),
                         "AC" + "0" * 32 + ":" + TWILIO_TOKEN)

    def test_provider_errors_surface_as_provider_error(self):
        self.server.responder = lambda p, b: (401, {"message": "Authenticate"})
        with self.assertRaises(ProviderError):
            twilio.TwilioMessagingProvider().send_text("+911", "hi")

    def test_missing_credentials_are_reported_clearly(self):
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": ""}), self.assertRaises(ProviderError) as ctx:
            twilio.TwilioMessagingProvider().send_text("+911", "hi")
        self.assertIn("auth_token", str(ctx.exception))

    def test_credentials_can_come_from_secrets_manager(self):
        boto3.client("secretsmanager", region_name=REGION).create_secret(
            Name="ecolink/twilio", SecretString=json.dumps({
                "account_sid": "AC" + "1" * 32, "auth_token": "from-secret",
                "whatsapp_from": "whatsapp:+1", "voice_from": "+1"}))
        env = {"TWILIO_SECRET_NAME": "ecolink/twilio"}
        with mock.patch.dict(os.environ, env):
            for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "TWILIO_VOICE_FROM"):
                os.environ.pop(var)
            twilio.TwilioMessagingProvider().send_text("+911", "hi")
        self.assertTrue(self.server.requests[-1]["path"].startswith("/Accounts/AC" + "1" * 32))

    def test_parse_inbound_whatsapp_form(self):
        body = ("From=whatsapp%3A%2B911&Body=hello&NumMedia=1&MediaUrl0=https%3A%2F%2Fapi.twilio.com%2Fm"
                "&MediaContentType0=image%2Fjpeg&Latitude=12.5&Longitude=77.5&MessageSid=SM1")
        msg = twilio.TwilioMessagingProvider().parse_inbound(WebhookRequest("POST", "u", {}, {}, body))
        self.assertEqual((msg.user_id, msg.text, msg.media_content_type), ("whatsapp:+911", "hello", "image/jpeg"))
        self.assertEqual((msg.latitude, msg.longitude, msg.message_id), (12.5, 77.5, "SM1"))

    def test_fetch_media_follows_twilio_redirect_without_sending_credentials(self):
        jpeg = b"\xff\xd8\xff" + b"x" * 10
        target = self.server.url + "/storage/signed-object"
        self.server.responder = lambda path, body: (
            (302, {}, {"Location": target}) if path.endswith("/Media/ME1")
            else (200, jpeg) if path == "/storage/signed-object" else (404, {}))
        data, content_type = twilio.TwilioMessagingProvider().fetch_media(self.server.url + "/Accounts/AC/Messages/MM1/Media/ME1")
        self.assertEqual((data, content_type), (jpeg, "image/jpeg"))
        first, second = self.server.requests
        self.assertIn("Authorization", first["headers"])
        self.assertNotIn("Authorization", second["headers"])  # the signed storage URL gets no credentials

    def test_delete_media_tolerates_already_deleted_and_reports_other_errors(self):
        url = self.server.url + "/Accounts/AC/Messages/MM1/Media/ME1"
        provider = twilio.TwilioMessagingProvider()
        self.server.responder = lambda p, b: (404, {"message": "gone"})
        provider.delete_media(url)  # 404 is fine
        self.server.responder = lambda p, b: (500, {"message": "boom"})
        with self.assertRaises(ProviderError):
            provider.delete_media(url)
        with self.assertRaises(ProviderError):
            provider.delete_media("https://evil.example.com/Media/ME1")

    def test_fetch_media_refuses_non_twilio_hosts(self):
        with self.assertRaises(ProviderError):
            twilio.TwilioMessagingProvider().fetch_media("https://evil.example.com/x.jpg")

    def test_offer_twiml_is_escaped_and_has_no_address(self):
        offer = {"pickup_id": "pk_1", "status": "offered", "slot": SLOT, "distance_km": 1.2,
                 "items": [{"label": "paper", "weight_kg": 2}, {"label": "<b>plastic</b>"}]}
        xml = twilio.build_offer_twiml(offer, "https://api.test/prod/voice/response?pickup_id=pk_1&x=1")
        self.assertIn("2 kg paper", xml)
        self.assertIn("&lt;b&gt;plastic", xml)
        self.assertIn("pickup_id=pk_1&amp;x=1", xml)
        self.assertNotIn("Rosewood", xml)


class ScriptTests(AwsTestCase):
    """scripts/seed.py and scripts/reset.py, run against the emulated tables."""

    seed_data = False

    def setUp(self):
        super().setUp()
        import reset
        import seed
        self.seed, self.reset = seed, reset

    def seed_args(self):
        return dict(prefix="EcoLink", recycler_phone=RECYCLER_PHONE, user_id=USER_ID)

    def test_seed_loads_8_recyclers_a_user_and_the_rate_table(self):
        counts = self.seed.seed_all(self.ddb, **self.seed_args())
        self.assertEqual((counts["recyclers"], counts["users"]), (8, 1))
        recyclers = self.repo.list_recyclers()
        self.assertEqual(len(recyclers), 8)
        self.assertEqual(sum(1 for r in recyclers if not r["available"]), 1)
        self.assertTrue(all(r["phone"] == RECYCLER_PHONE for r in recyclers))
        self.assertEqual(self.repo.get_user(USER_ID)["user_id"], USER_ID)
        self.assertIsNotNone(self.repo.get_scrap_rate("paper"))
        self.assertIsNotNone(self.repo.get_ewaste_rate("laptop"))
        self.assertEqual(self.repo.get_condition_factor("working"), 1.0)
        self.seed.seed_all(self.ddb, **self.seed_args())  # idempotent
        self.assertEqual(len(self.repo.list_recyclers()), 8)

    def test_reset_wipes_runtime_data_and_restores_the_demo_state(self):
        self.seed.seed_all(self.ddb, **self.seed_args())
        pid = self.new_pickup()
        self.repo.ensure_user("whatsapp:+910000000009")
        self.ddb.Table("EcoLink-conversation-state").put_item(Item={"user_id": USER_ID, "path": "regular"})
        self.ddb.Table("EcoLink-recyclers").update_item(
            Key={"recycler_id": "R001"}, UpdateExpression="SET available = :f", ExpressionAttributeValues={":f": False})

        self.reset.reset_all(self.ddb, **self.seed_args())

        self.assertIsNone(self.repo.get_pickup(pid))
        self.assertIsNone(self.repo.get_user("whatsapp:+910000000009"))
        self.assertEqual(self.ddb.Table("EcoLink-conversation-state").scan()["Items"], [])
        self.assertTrue(self.repo.get_recycler("R001")["available"])
        self.assertEqual(len(self.repo.list_recyclers()), 8)

    def test_argument_validation(self):
        import argparse
        parser = argparse.ArgumentParser()
        self.seed.add_common_args(parser)
        for bad in (["--recycler-phone", "98123", "--user-id", USER_ID],
                    ["--recycler-phone", RECYCLER_PHONE, "--user-id", "+919812345678"]):
            with self.subTest(args=bad), self.assertRaises(SystemExit):
                self.seed.validate_args(parser.parse_args(bad), parser)

    def test_empty_bucket(self):
        s3 = boto3.client("s3", region_name=REGION)
        for i in range(3):
            s3.put_object(Bucket="ecolink-test-images", Key=f"images/x/{i}.jpg", Body=b"x")
        self.assertEqual(self.reset.empty_bucket(s3, "ecolink-test-images"), 3)
        self.assertNotIn("Contents", s3.list_objects_v2(Bucket="ecolink-test-images"))


if __name__ == "__main__":
    unittest.main()
