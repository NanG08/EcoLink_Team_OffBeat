"""Recycler matching: label filter, availability, distance sort.

Pure-function tests use plain data; handler tests run the real handler + Repository against
the seeded demo recyclers (8 of them, see scripts/demo_data.py).
"""

from __future__ import annotations

import unittest

from ecolink.core.matching import haversine_km, match_recyclers
from ecolink.handlers import recyclers_match
from tests.support import AwsTestCase, api_event, invoke

LAT, LNG = 12.9716, 77.5946  # ~0.011 degrees of latitude is ~1.2 km


def recycler(rid, dlat, accepts, *, available=True):
    return {"recycler_id": rid, "name": f"Recycler {rid}", "lat": LAT + dlat, "lng": LNG,
            "accepts": accepts, "available": available, "phone": "+910000000000"}


RECYCLERS = [
    recycler("R001", 0.011, ["paper", "cardboard", "plastic"]),
    recycler("R002", 0.022, ["paper", "plastic", "metal", "glass"]),
    recycler("R003", 0.005, ["metal"]),
    recycler("R004", 0.001, ["paper", "plastic"], available=False),  # nearest, but unavailable
    recycler("R005", 0.040, ["ewaste", "battery"]),
]


class HaversineTests(unittest.TestCase):
    def test_zero_distance(self):
        self.assertAlmostEqual(haversine_km(LAT, LNG, LAT, LNG), 0.0, places=6)

    def test_delhi_to_mumbai(self):
        self.assertAlmostEqual(haversine_km(28.6139, 77.2090, 19.0760, 72.8777), 1148, delta=10)

    def test_symmetric(self):
        self.assertAlmostEqual(haversine_km(12.9, 77.5, 13.1, 77.7), haversine_km(13.1, 77.7, 12.9, 77.5), places=9)


class MatchTests(unittest.TestCase):
    def ids(self, rows):
        return [r["recycler_id"] for r in rows]

    def test_filters_by_accepted_label_and_sorts_by_distance(self):
        rows = match_recyclers(RECYCLERS, LAT, LNG, ["paper", "plastic"])
        self.assertEqual(self.ids(rows), ["R001", "R002"])
        self.assertLess(rows[0]["distance_km"], rows[1]["distance_km"])

    def test_default_mode_requires_all_labels(self):
        self.assertEqual(self.ids(match_recyclers(RECYCLERS, LAT, LNG, ["paper", "metal"])), ["R002"])

    def test_any_mode_accepts_partial_overlap(self):
        rows = match_recyclers(RECYCLERS, LAT, LNG, ["paper", "metal"], mode="any")
        self.assertEqual(self.ids(rows), ["R003", "R001", "R002"])

    def test_unavailable_recyclers_are_excluded(self):
        self.assertNotIn("R004", self.ids(match_recyclers(RECYCLERS, LAT, LNG, ["paper"])))

    def test_missing_available_flag_is_treated_as_unavailable(self):
        r = recycler("R010", 0, ["paper"])
        del r["available"]
        self.assertEqual(match_recyclers([r], LAT, LNG, ["paper"]), [])

    def test_hazardous_labels_match_only_dropoff_points(self):
        self.assertEqual(self.ids(match_recyclers(RECYCLERS, LAT, LNG, ["battery"])), ["R005"])

    def test_max_km_and_limit(self):
        self.assertEqual(self.ids(match_recyclers(RECYCLERS, LAT, LNG, ["paper"], max_km=1.5)), ["R001"])
        self.assertEqual(len(match_recyclers(RECYCLERS, LAT, LNG, ["paper"], limit=1)), 1)

    def test_no_match_returns_empty_list(self):
        self.assertEqual(match_recyclers(RECYCLERS, LAT, LNG, ["glass", "battery"]), [])

    def test_recyclers_without_coordinates_are_skipped(self):
        broken = {"recycler_id": "R011", "name": "x", "accepts": ["paper"], "available": True}
        self.assertEqual(match_recyclers([broken], LAT, LNG, ["paper"]), [])

    def test_distance_ties_break_on_recycler_id(self):
        twins = [recycler("R021", 0.01, ["paper"]), recycler("R020", 0.01, ["paper"])]
        self.assertEqual(self.ids(match_recyclers(twins, LAT, LNG, ["paper"])), ["R020", "R021"])

    def test_output_has_exactly_the_contract_keys_and_no_private_data(self):
        row = match_recyclers(RECYCLERS, LAT, LNG, ["paper"])[0]
        self.assertEqual(set(row), {"recycler_id", "name", "distance_km", "accepts"})


class MatchHandlerTests(AwsTestCase):
    """GET /recyclers/match against the seeded DynamoDB table."""

    def match(self, **q):
        return invoke(recyclers_match.handler, api_event("GET", "/recyclers/match", query=q))

    def test_seeded_recyclers_sorted_nearest_first_and_unavailable_hidden(self):
        status, rows = self.match(lat=str(LAT), lng=str(LNG), labels="paper,plastic", limit="20")
        self.assertEqual(status, 200)
        distances = [r["distance_km"] for r in rows]
        self.assertEqual(distances, sorted(distances))
        ids = [r["recycler_id"] for r in rows]
        self.assertNotIn("R008", ids)  # seeded with available=False, and it is the 2nd nearest
        self.assertNotIn("R005", ids)  # e-waste hub does not take paper/plastic
        self.assertEqual(ids[0], "R001")
        for r in rows:
            self.assertEqual(set(r), {"recycler_id", "name", "distance_km", "accepts"})
            self.assertTrue({"paper", "plastic"} <= set(r["accepts"]))

    def test_hazardous_labels_return_authorised_dropoff_points(self):
        _, rows = self.match(lat=str(LAT), lng=str(LNG), labels="battery")
        self.assertEqual([r["recycler_id"] for r in rows], ["R005", "R007"])

    def test_availability_change_in_the_table_takes_effect(self):
        self.ddb.Table("EcoLink-recyclers").update_item(
            Key={"recycler_id": "R001"}, UpdateExpression="SET available = :f",
            ExpressionAttributeValues={":f": False})
        _, rows = self.match(lat=str(LAT), lng=str(LNG), labels="paper")
        self.assertNotIn("R001", [r["recycler_id"] for r in rows])

    def test_bad_input_gives_clear_400s(self):
        for q in ({}, {"lat": "1", "lng": "2"}, {"lat": "x", "lng": "2", "labels": "paper"},
                  {"lat": "1", "lng": "2", "labels": "gold"}, {"lat": "91", "lng": "2", "labels": "paper"},
                  {"lat": "1", "lng": "2", "labels": "paper", "mode": "some"}):
            with self.subTest(q=q):
                status, body = self.match(**q)
                self.assertEqual((status, body["error"]), (400, "invalid_request"))
                self.assertTrue(body["detail"])


if __name__ == "__main__":
    unittest.main()
