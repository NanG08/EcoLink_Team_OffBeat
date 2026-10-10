"""Local ML fallbacks: the ONNX vision model and the fallback rate table, loaded from S3.

Uses the real artifacts shipped in the repo (ml/vision/artifacts/weights/best.onnx and
ml/pricing/rate_table.json), uploaded to an emulated models bucket exactly as the CDK stack does.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import boto3

from ecolink.core import classifier, fallback_rates, vision_fallback
from ecolink.core.constants import HAZARDOUS_LABELS, LABELS
from ecolink.core.http import ApiError
from ecolink.core.model_store import ModelStore, ModelUnavailable
from ecolink.handlers import pricing, proxy
from tests.support import REGION, AwsTestCase, api_event, invoke

try:
    import numpy as np
    from PIL import Image
    import onnxruntime  # noqa: F401
    HAVE_ML = True
except ImportError:  # the ML runtime is optional for the rest of the suite
    HAVE_ML = False

ROOT = Path(__file__).resolve().parent.parent
ONNX = ROOT / "ml" / "vision" / "artifacts" / "weights" / "best.onnx"
RATE_TABLE = ROOT / "ml" / "pricing" / "rate_table.json"
NAMES = {i: n for i, n in enumerate(LABELS)}  # EcoLink's frozen class order, as stored in the ONNX file


def jpeg(width=640, height=480, color=(120, 130, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


def fake_output(rows: list[tuple[float, float, float, float, int, float]]) -> "np.ndarray":
    """A raw YOLOv8 output (1, 4+7, N) holding the given (cx, cy, w, h, class_id, score) anchors."""
    out = np.zeros((1, 4 + len(LABELS), len(rows)), dtype=np.float32)
    for i, (cx, cy, w, h, class_id, score) in enumerate(rows):
        out[0, 0:4, i] = (cx, cy, w, h)
        out[0, 4 + class_id, i] = score
    return out


@unittest.skipUnless(HAVE_ML, "numpy, Pillow and onnxruntime are required")
class PostProcessingTests(unittest.TestCase):
    """The decode / NMS maths, fed with hand-built YOLO output tensors."""

    def test_letterbox_keeps_aspect_ratio_and_pads_with_114(self):
        canvas, ratio, (left, top) = vision_fallback.letterbox(Image.new("RGB", (640, 480), (0, 0, 0)), 416)
        self.assertEqual(canvas.shape, (416, 416, 3))
        self.assertAlmostEqual(ratio, 0.65)
        self.assertEqual((left, top), (0, 52))
        self.assertTrue((canvas[0] == 114).all() and (canvas[-1] == 114).all())  # padded rows
        self.assertTrue((canvas[52:364] == 0).all())  # the image itself

    def test_resize_uses_opencv_style_half_pixel_bilinear(self):
        # 2 pixels (0 and 100) stretched to 4: OpenCV samples at (i+0.5)/2-0.5 and clamps at the edge
        arr = np.array([[[0, 0, 0], [100, 100, 100]]], dtype=np.uint8)
        got = vision_fallback.resize_bilinear(arr, 4, 1)
        self.assertEqual(got[0, :, 0].tolist(), [0, 25, 75, 100])
        flat = np.full((50, 80, 3), 77, dtype=np.uint8)
        self.assertTrue((vision_fallback.resize_bilinear(flat, 33, 21) == 77).all())  # constants stay constant

    def test_resize_matches_opencv_when_available(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")
        arr = np.random.default_rng(0).integers(0, 256, (300, 200, 3), dtype=np.uint8)
        for new_w, new_h in ((100, 150), (400, 600), (37, 41), (199, 299)):
            ours = vision_fallback.resize_bilinear(arr, new_w, new_h).astype(int)
            theirs = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_LINEAR).astype(int)
            self.assertLessEqual(int(np.abs(ours - theirs).max()), 1, (new_w, new_h))

    def test_input_tensor_is_normalised_nchw(self):
        canvas, _, _ = vision_fallback.letterbox(Image.new("RGB", (416, 416), (255, 0, 0)), 416)
        tensor = vision_fallback.to_input_tensor(canvas)
        self.assertEqual(tensor.shape, (1, 3, 416, 416))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), 1.0)
        self.assertAlmostEqual(float(tensor[0, 1, 0, 0]), 0.0)

    def test_nms_suppresses_overlaps_but_keeps_distant_boxes(self):
        boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [300, 300, 400, 400]], dtype=np.float32)
        scores = np.array([0.8, 0.9, 0.7], dtype=np.float32)
        self.assertEqual(vision_fallback.nms(boxes, scores, 0.45), [1, 2])

    def test_decode_threshold_dedupe_hazard_and_order(self):
        out = fake_output([
            (100, 100, 50, 50, 6, 0.90),   # battery, strongest
            (102, 101, 50, 50, 6, 0.80),   # battery, same place -> removed by NMS
            (300, 300, 60, 60, 0, 0.60),   # paper
            (200, 50, 40, 40, 4, 0.30),    # glass, below the 0.50 threshold
            (50, 300, 40, 40, 2, 0.55),    # plastic (weaker)
            (350, 60, 40, 40, 2, 0.70),    # plastic elsewhere (stronger) -> one entry per label, the best
        ])
        self.assertEqual(vision_fallback.decode(out, NAMES), [
            {"label": "battery", "confidence": 0.9, "hazardous": True},
            {"label": "plastic", "confidence": 0.7, "hazardous": False},
            {"label": "paper", "confidence": 0.6, "hazardous": False},
        ])

    def test_decode_returns_nothing_when_no_score_reaches_the_threshold(self):
        self.assertEqual(vision_fallback.decode(fake_output([(100, 100, 50, 50, 3, 0.49)]), NAMES), [])

    def test_decode_drops_classes_without_an_ecolink_label(self):
        out = fake_output([(100, 100, 50, 50, 0, 0.9)])
        self.assertEqual(vision_fallback.decode(out, {0: "bus"}), [])

    def test_hazardous_flag_matches_the_contract(self):
        for class_id, label in enumerate(LABELS):
            (item,) = vision_fallback.decode(fake_output([(100, 100, 50, 50, class_id, 0.9)]), NAMES)
            self.assertEqual((item["label"], item["hazardous"]), (label, label in HAZARDOUS_LABELS))

    def test_class_names_come_from_the_onnx_metadata_string(self):
        self.assertEqual(vision_fallback.parse_names("{0: 'paper', 1: 'cardboard'}"), {0: "paper", 1: "cardboard"})
        self.assertEqual(vision_fallback.parse_names(None), NAMES)
        self.assertEqual(vision_fallback.parse_names("not a dict"), NAMES)


@unittest.skipUnless(HAVE_ML and ONNX.is_file() and RATE_TABLE.is_file(), "model artifacts / ML runtime not available")
class ModelFallbackTests(AwsTestCase):
    """Real weights, served from an emulated models bucket."""

    def setUp(self):
        super().setUp()
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="ecolink-test-models", CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.upload_file(str(ONNX), "ecolink-test-models", "vision/best.onnx")
        s3.upload_file(str(RATE_TABLE), "ecolink-test-models", "pricing/rate_table.json")
        cache = tempfile.mkdtemp()
        env = {"MODELS_BUCKET": "ecolink-test-models", "VISION_MODEL_KEY": "vision/best.onnx",
               "PRICING_TABLE_KEY": "pricing/rate_table.json", "MODEL_VERSION": "test", "MODEL_CACHE_DIR": cache}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        vision_fallback.reset()
        fallback_rates.reset()
        self.addCleanup(vision_fallback.reset)
        self.addCleanup(fallback_rates.reset)

    # -- model store ----------------------------------------------------------
    def test_model_is_downloaded_once_and_cached(self):
        store = ModelStore()
        first = store.local_path("vision/best.onnx")
        stamp = first.stat().st_mtime_ns
        self.assertEqual(first.stat().st_size, ONNX.stat().st_size)
        self.assertEqual(store.local_path("vision/best.onnx").stat().st_mtime_ns, stamp)  # not re-downloaded

    def test_missing_key_or_bucket_is_reported_as_unavailable(self):
        with self.assertRaises(ModelUnavailable):
            ModelStore().local_path("vision/does-not-exist.onnx")
        with mock.patch.dict(os.environ, {"MODELS_BUCKET": ""}), self.assertRaises(ModelUnavailable):
            ModelStore()

    # -- the real model -------------------------------------------------------
    def test_real_onnx_model_loads_with_the_expected_signature(self):
        detector = vision_fallback.get_detector()
        self.assertEqual(detector.size, 416)
        self.assertEqual([detector.names[i] for i in range(7)], list(LABELS))

    def test_real_model_returns_contract_shaped_items_deterministically(self):
        data = jpeg()
        first = vision_fallback.classify_bytes(data)
        self.assertEqual(first, vision_fallback.classify_bytes(data))
        confidences = [i["confidence"] for i in first]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        for item in first:
            self.assertEqual(set(item), {"label", "confidence", "hazardous"})
            self.assertIn(item["label"], LABELS)
            self.assertTrue(0.5 <= item["confidence"] <= 1.0)
            self.assertEqual(item["hazardous"], item["label"] in HAZARDOUS_LABELS)
        self.assertEqual(len({i["label"] for i in first}), len(first))  # one entry per label

    def test_real_model_handles_png_and_unusual_sizes(self):
        buf = io.BytesIO()
        Image.new("RGBA", (37, 900), (10, 200, 30, 255)).save(buf, "PNG")  # tall, with alpha
        self.assertIsInstance(vision_fallback.classify_bytes(buf.getvalue()), list)

    def test_corrupt_image_is_a_client_error(self):
        with self.assertRaises(ApiError) as ctx:
            vision_fallback.classify_bytes(b"\xff\xd8\xff" + b"garbage")
        self.assertEqual(ctx.exception.status, 400)

    # -- classifier chain -----------------------------------------------------
    class Bedrock:
        def __init__(self, fail=None):
            self.calls = 0
            self.fail = fail

        def converse(self, **kwargs):
            self.calls += 1
            if self.fail:
                raise self.fail
            return {"output": {"message": {"content": [{"text": '{"items":[{"label":"metal","confidence":0.8}]}'}]}}}

    def classify(self, bedrock, **env):
        with mock.patch.dict(os.environ, {"CLASSIFY_MODEL_ID": "amazon.nova-pro-v1:0", **env}), \
             mock.patch.object(classifier, "fetch_image", return_value=jpeg()):
            return classifier.classify("https://b.s3.amazonaws.com/x.jpg", client=bedrock)

    def test_bedrock_answers_when_it_works(self):
        bedrock = self.Bedrock()
        result = self.classify(bedrock)
        self.assertEqual((result["source"], result["items"][0]["label"]), ("bedrock", "metal"))

    def test_onnx_answers_when_bedrock_is_denied(self):
        denied = RuntimeError("AccessDeniedException: Your account is currently being verified")
        bedrock = self.Bedrock(fail=denied)
        result = self.classify(bedrock)
        self.assertEqual(bedrock.calls, 1)
        self.assertEqual(result["source"], "onnx-yolov8n")
        self.assertEqual(result["items"], vision_fallback.classify_bytes(jpeg()))

    def test_onnx_first_when_configured_and_bedrock_is_not_called(self):
        bedrock = self.Bedrock()
        result = self.classify(bedrock, CLASSIFY_PRIMARY="onnx")
        self.assertEqual(result["source"], "onnx-yolov8n")
        self.assertEqual(bedrock.calls, 0)

    def test_bedrock_is_the_backup_when_the_local_model_is_missing(self):
        boto3.client("s3", region_name=REGION).delete_object(Bucket="ecolink-test-models", Key="vision/best.onnx")
        bedrock = self.Bedrock()
        result = self.classify(bedrock, CLASSIFY_PRIMARY="onnx")
        self.assertEqual((result["source"], bedrock.calls), ("bedrock", 1))

    def test_error_is_raised_when_every_classifier_fails(self):
        boto3.client("s3", region_name=REGION).delete_object(Bucket="ecolink-test-models", Key="vision/best.onnx")
        with self.assertRaises(ApiError) as ctx:
            self.classify(self.Bedrock(fail=RuntimeError("down")))
        self.assertEqual((ctx.exception.status, ctx.exception.code), (502, "classifier_error"))  # the first failure

    def test_only_the_local_model_when_bedrock_is_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLASSIFY_MODEL_ID", None)
            with mock.patch.object(classifier, "fetch_image", return_value=jpeg()):
                result = classifier.classify("https://b.s3.amazonaws.com/x.jpg")
        self.assertEqual(result["source"], "onnx-yolov8n")

    def test_classify_route_returns_the_contract_through_the_handler(self):
        with mock.patch.dict(os.environ, {"CLASSIFY_PRIMARY": "onnx"}), \
             mock.patch.object(classifier, "fetch_image", return_value=jpeg()):
            status, body = invoke(proxy.handler, api_event(
                "POST", "/classify", {"image_url": "https://b.s3.amazonaws.com/x.jpg"}, ))
        self.assertEqual(status, 200)
        self.assertIsInstance(body["items"], list)
        self.assertEqual(body["source"], "onnx-yolov8n")

    # -- pricing fallback -----------------------------------------------------
    def price(self, label="paper", weight="3"):
        return invoke(pricing.handler, api_event("GET", "/price", query={"label": label, "weight_kg": weight}))

    def quote(self, **body):
        return invoke(pricing.handler, api_event("POST", "/ewaste/quote", body))

    def test_fallback_table_matches_the_deployed_file(self):
        raw = json.loads(RATE_TABLE.read_text(encoding="utf-8"))
        self.assertEqual(fallback_rates.scrap_rate("paper")["low_per_kg"], raw["scrap"]["paper"]["low_per_kg"])
        self.assertEqual(fallback_rates.condition_factor("broken"), raw["ewaste"]["condition_factors"]["broken"])
        self.assertIsNone(fallback_rates.scrap_rate("battery"))

    def test_price_uses_the_fallback_table_when_dynamodb_has_no_rate(self):
        self.ddb.Table("EcoLink-rate-table").delete_item(Key={"kind": "scrap", "item": "paper"})
        status, body = self.price()
        self.assertEqual(status, 200)
        self.assertEqual((body["low"], body["high"], body["currency"]), (30, 42, "INR"))

    def test_price_survives_a_dynamodb_failure(self):
        self.ddb.Table("EcoLink-rate-table").delete()  # DynamoDB now raises ResourceNotFoundException
        status, body = self.price("plastic", "2")
        self.assertEqual(status, 200)
        self.assertEqual((body["low"], body["high"]), (16, 36))

    def test_ewaste_quote_survives_a_dynamodb_failure(self):
        self.ddb.Table("EcoLink-rate-table").delete()
        status, body = self.quote(item_type="laptop", age_years=2, condition="working")
        self.assertEqual((status, body), (200, {"low": 10500, "high": 24500, "recommendation": "sell"}))
        self.assertEqual(self.quote(item_type="toaster", age_years=1, condition="working")[0], 200)  # "other" row

    def test_dynamodb_is_still_preferred_when_it_has_the_rate(self):
        self.ddb.Table("EcoLink-rate-table").put_item(
            Item={"kind": "scrap", "item": "paper", "low_per_kg": 100, "high_per_kg": 200, "updated": "2026-10-11"})
        self.assertEqual(self.price()[1]["low"], 300)

    def test_hazardous_and_invalid_requests_are_still_rejected(self):
        self.ddb.Table("EcoLink-rate-table").delete()
        self.assertEqual(self.price("battery", "1")[1]["error"], "hazardous_label")
        self.assertEqual(self.price("gold", "1")[0], 400)
        self.assertEqual(self.quote(item_type="Lithium battery", age_years=1, condition="working")[1]["recommendation"], "dropoff")
        self.assertEqual(self.quote(item_type="laptop", age_years=1, condition="haunted")[0], 400)

    def test_no_fallback_configured_means_the_old_404(self):
        with mock.patch.dict(os.environ, {"MODELS_BUCKET": ""}):
            fallback_rates.reset()
            self.ddb.Table("EcoLink-rate-table").delete_item(Key={"kind": "scrap", "item": "glass"})
            self.assertEqual(self.price("glass", "1")[0], 404)


if __name__ == "__main__":
    unittest.main()
