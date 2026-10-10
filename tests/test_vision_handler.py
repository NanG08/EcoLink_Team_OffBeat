"""
Tests for ml.vision.handler — classify(), handler() and the Lambda proxy handler.

handler.py pre-loads the model at import time, so the module is imported with
load_model patched out: no weights, no network.
"""
import importlib
import json
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.vision.inference import InferenceError


@pytest.fixture(scope="module")
def handler_mod():
    with mock.patch("ml.vision.model_loader.load_model", side_effect=RuntimeError("no weights in tests")):
        import ml.vision.handler as h
        h = importlib.reload(h)
    return h


_RESULT = {"items": [{"label": "plastic", "confidence": 0.91, "hazardous": False}]}
_URL = "https://bucket.s3.amazonaws.com/img.jpg"


# ── import-time behaviour ─────────────────────────────────────────────────────

def test_preload_failure_is_non_fatal(handler_mod) -> None:
    """A failed warm-start load must not prevent the module from importing."""
    assert handler_mod._MODEL is None
    assert callable(handler_mod.lambda_handler)


# ── classify() ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("request_body", [{}, {"image_url": ""}, {"image_url": "   "}])
def test_classify_requires_image_url(handler_mod, request_body) -> None:
    with pytest.raises(ValueError, match="image_url"):
        handler_mod.classify(request_body)


def test_classify_strips_and_delegates(handler_mod) -> None:
    with mock.patch("ml.vision.inference.classify_image_url", return_value=_RESULT) as m:
        assert handler_mod.classify({"image_url": f"  {_URL}  "}) == _RESULT
    m.assert_called_once_with(_URL)


def test_handler_direct_callable(handler_mod) -> None:
    with mock.patch("ml.vision.inference.classify_image_url", return_value=_RESULT):
        assert handler_mod.handler(_URL) == _RESULT


# ── lambda_handler() ──────────────────────────────────────────────────────────

def test_lambda_200_with_json_string_body(handler_mod) -> None:
    with mock.patch("ml.vision.inference.classify_image_url", return_value=_RESULT):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": _URL})}, None)
    assert out["statusCode"] == 200
    assert out["headers"]["Content-Type"] == "application/json"
    assert json.loads(out["body"]) == _RESULT


def test_lambda_200_with_dict_body(handler_mod) -> None:
    with mock.patch("ml.vision.inference.classify_image_url", return_value=_RESULT):
        out = handler_mod.lambda_handler({"body": {"image_url": _URL}}, None)
    assert out["statusCode"] == 200


@pytest.mark.parametrize("event", [{}, {"body": None}, {"body": "{}"}, {"body": json.dumps({"image_url": " "})}])
def test_lambda_400_when_image_url_missing(handler_mod, event) -> None:
    out = handler_mod.lambda_handler(event, None)
    assert out["statusCode"] == 400
    assert "image_url" in json.loads(out["body"])["error"]


def test_lambda_propagates_inference_error_status(handler_mod) -> None:
    err = InferenceError("image_url must use HTTPS", status_code=400)
    with mock.patch("ml.vision.inference.classify_image_url", side_effect=err):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": _URL})}, None)
    assert out["statusCode"] == 400
    assert "HTTPS" in json.loads(out["body"])["error"]


def test_lambda_502_on_upstream_fetch_failure(handler_mod) -> None:
    err = InferenceError("HTTP 403 fetching image", status_code=502)
    with mock.patch("ml.vision.inference.classify_image_url", side_effect=err):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": _URL})}, None)
    assert out["statusCode"] == 502


def test_lambda_500_on_unexpected_error(handler_mod) -> None:
    with mock.patch("ml.vision.inference.classify_image_url", side_effect=RuntimeError("boom")):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": _URL})}, None)
    assert out["statusCode"] == 500


def test_lambda_malformed_json_body_is_an_error_not_a_crash(handler_mod) -> None:
    out = handler_mod.lambda_handler({"body": "{not json"}, None)
    assert out["statusCode"] >= 400
    assert "error" in json.loads(out["body"])


def test_lambda_rejects_non_https_url_end_to_end(handler_mod) -> None:
    """Real validation path: http:// is refused with 400 before any model is loaded."""
    with mock.patch("ml.vision.inference.get_model", side_effect=AssertionError("model must not load")):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": "http://b.s3.amazonaws.com/x.jpg"})}, None)
    assert out["statusCode"] == 400


def test_lambda_corrupt_image_is_400_not_500(handler_mod) -> None:
    """A JPEG-magic payload that cannot be decoded is a client error."""
    resp = mock.MagicMock()
    resp.read.return_value = b"\xff\xd8\xff" + b"garbage" * 20
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    with mock.patch("ml.vision.safety.urllib.request.urlopen", return_value=resp), \
         mock.patch("ml.vision.inference.get_model", return_value=mock.Mock(side_effect=AssertionError("model must not run"))):
        out = handler_mod.lambda_handler({"body": json.dumps({"image_url": _URL})}, None)
    assert out["statusCode"] == 400
