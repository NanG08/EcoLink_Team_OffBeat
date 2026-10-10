"""
Tests for ml/serving/lambda_handler.py — the Lambda container entry point.
Unit tests mock the pipeline; the integration test runs the real model.
"""
import base64
import io
import json
import os
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.pricing import rates
from ml.serving import lambda_handler as lh
from ml.vision.inference import InferenceError
from ml.vision.schemas import ClassifyResponse

_URL = "https://bucket.s3.amazonaws.com/img.jpg"
_REPO = Path(__file__).resolve().parent.parent


def rest(method, path, body=None, query=None, b64=False):
    raw = json.dumps(body) if body is not None and not isinstance(body, str) else body
    if b64 and raw is not None:
        raw = base64.b64encode(raw.encode()).decode()
    return {"httpMethod": method, "resource": path, "path": path, "body": raw,
            "isBase64Encoded": b64, "queryStringParameters": query}


def http_v2(method, path, body=None, query=None):
    return {"version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method}},
            "body": json.dumps(body) if body is not None else None, "queryStringParameters": query}


def call(event):
    resp = lh.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]), resp["headers"]


def fake_items(*pairs):
    hazardous = {"ewaste", "battery"}
    return ClassifyResponse(items=[{"label": l, "confidence": c, "hazardous": l in hazardous} for l, c in pairs])


# ── POST /classify ────────────────────────────────────────────────────────────

def test_classify_adds_confirmation_flag_and_keeps_contract_fields() -> None:
    items = fake_items(("plastic", 0.91), ("battery", 0.72), ("glass", 0.55))
    with mock.patch.object(lh, "run_inference", return_value=items) as run:
        status, body, headers = call(rest("POST", "/classify", {"image_url": _URL}))
    run.assert_called_once_with(_URL)
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert "Access-Control-Allow-Origin" in headers
    assert body == {"items": [
        {"label": "plastic", "confidence": 0.91, "hazardous": False, "requires_confirmation": False},
        {"label": "battery", "confidence": 0.72, "hazardous": True, "requires_confirmation": False},
        {"label": "glass", "confidence": 0.55, "hazardous": False, "requires_confirmation": True},
    ]}


@pytest.mark.parametrize("label,conf,expected", [
    ("plastic", 0.79, True), ("plastic", 0.80, False),     # regular items: confirm below 0.80
    ("paper", 0.50, True), ("metal", 0.99, False),
    ("battery", 0.69, True), ("battery", 0.70, False),     # hazardous: confirm below 0.70
    ("ewaste", 0.50, True), ("ewaste", 0.95, False),
])
def test_confirmation_thresholds_follow_pipeline_rules(label, conf, expected) -> None:
    with mock.patch.object(lh, "run_inference", return_value=fake_items((label, conf))):
        _, body, _ = call(rest("POST", "/classify", {"image_url": _URL}))
    assert body["items"][0]["requires_confirmation"] is expected


def test_empty_result_is_valid() -> None:
    with mock.patch.object(lh, "run_inference", return_value=ClassifyResponse(items=[])):
        assert call(rest("POST", "/classify", {"image_url": _URL}))[:2] == (200, {"items": []})


@pytest.mark.parametrize("status,code", [(400, "invalid_image"), (502, "image_fetch_failed")])
def test_pipeline_errors_use_backend_error_format(status, code) -> None:
    with mock.patch.object(lh, "run_inference", side_effect=InferenceError("bad image", status_code=status)):
        got_status, body, _ = call(rest("POST", "/classify", {"image_url": _URL}))
    assert got_status == status
    assert body == {"error": code, "detail": "bad image"}


@pytest.mark.parametrize("event", [
    rest("POST", "/classify", {}),
    rest("POST", "/classify", {"image_url": "   "}),
    rest("POST", "/classify", {"image_url": 42}),
    rest("POST", "/classify", "{not json"),
    rest("POST", "/classify", "[1, 2]"),
    rest("POST", "/classify", None),
])
def test_bad_requests_are_400_and_never_reach_the_model(event) -> None:
    with mock.patch.object(lh, "run_inference", side_effect=AssertionError("must not run")):
        status, body, _ = call(event)
    assert status == 400 and body["error"] == "invalid_request" and body["detail"]


def test_real_validation_rejects_http_url_without_loading_model() -> None:
    with mock.patch("ml.vision.inference.get_model", side_effect=AssertionError("must not load")):
        status, body, _ = call(rest("POST", "/classify", {"image_url": "http://b.s3.amazonaws.com/x.jpg"}))
    assert (status, body["error"]) == (400, "invalid_image") and "HTTPS" in body["detail"]


def test_unexpected_errors_are_500_without_internals() -> None:
    with mock.patch.object(lh, "run_inference", side_effect=RuntimeError("secret stack detail")):
        status, body, _ = call(rest("POST", "/classify", {"image_url": _URL}))
    assert status == 500 and body["error"] == "internal_error"
    assert "secret" not in body["detail"]


@pytest.mark.parametrize("event", [
    rest("POST", "/classify", {"image_url": _URL}, b64=True),
    http_v2("POST", "/classify", {"image_url": _URL}),
    {"image_url": _URL},                                      # direct Lambda invoke
    rest("POST", "/prod/classify/", {"image_url": _URL}),     # stage prefix + trailing slash
])
def test_event_shapes(event) -> None:
    with mock.patch.object(lh, "run_inference", return_value=fake_items(("metal", 0.9))) as run:
        status, body, _ = call(event)
    assert status == 200 and body["items"][0]["label"] == "metal"
    run.assert_called_once_with(_URL)


@pytest.mark.parametrize("event", [rest("GET", "/classify"), rest("POST", "/nope", {}), rest("DELETE", "/price")])
def test_unknown_routes_404(event) -> None:
    assert call(event)[:2] == (404, {"error": "not_found", "detail": "Unknown route"})


# ── GET /price, POST /ewaste/quote, GET /health ───────────────────────────────

def test_price_route_matches_rate_table() -> None:
    status, body, _ = call(rest("GET", "/price", query={"label": "paper", "weight_kg": "3"}))
    assert status == 200 and body == rates.price_estimate("paper", 3)
    assert set(body) == {"low", "high", "currency", "updated"}
    status, body, _ = call(http_v2("GET", "/price", query={"label": "battery", "weight_kg": "1"}))
    assert (status, body["error"]) == (422, "hazardous_label")
    assert call(rest("GET", "/price"))[0] == 400


def test_quote_route_matches_rate_table() -> None:
    req = {"item_type": "laptop", "age_years": 2, "condition": "working"}
    status, body, _ = call(rest("POST", "/ewaste/quote", req))
    assert status == 200 and body == {"low": 10500, "high": 24500, "recommendation": "sell"}
    _, body, _ = call(rest("POST", "/ewaste/quote", {"item_type": "power bank", "age_years": 0, "condition": "working"}))
    assert body["recommendation"] == "dropoff"
    assert call(rest("POST", "/ewaste/quote", {"item_type": "laptop", "age_years": 1}))[0] == 400


def test_health_does_not_trigger_a_model_load() -> None:
    from ml.vision import model_loader

    model_loader.reset_model()
    with mock.patch.object(model_loader, "load_model", side_effect=AssertionError("must not load")):
        status, body, _ = call(rest("GET", "/health"))
    assert status == 200
    assert body["model_loaded"] is False and body["rates_are_placeholders"] is True


def test_import_does_not_preload_model_outside_lambda() -> None:
    import importlib

    from ml.vision import model_loader

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
        os.environ.pop("ECOLINK_PRELOAD_MODEL", None)
        with mock.patch.object(model_loader, "get_model", side_effect=AssertionError("must not preload")):
            importlib.reload(lh)


# ── container files ───────────────────────────────────────────────────────────

def test_container_files_are_consistent() -> None:
    dockerfile = (_REPO / "ml" / "serving" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["ml.serving.lambda_handler.handler"]' in dockerfile
    lock = (_REPO / "ml" / "serving" / "requirements-lambda.txt").read_text(encoding="utf-8")
    pins = {l.split("==")[0].lower(): l.split("==")[1].strip() for l in lock.splitlines() if "==" in l and not l.startswith("#")}
    assert "opencv-python" not in pins and "opencv-python-headless" in pins   # no libGL in Lambda
    assert pins["torch"].endswith("+cpu")
    tested = {l.split("==")[0].lower(): l.split("==")[1].strip()
              for l in (_REPO / "ml" / "vision" / "requirements.txt").read_text().splitlines() if "==" in l and not l.startswith("#")}
    for pkg in ("ultralytics", "numpy", "pillow", "pyyaml", "matplotlib", "requests"):
        assert pins[pkg] == tested[pkg], f"{pkg}: image {pins[pkg]} != tested {tested[pkg]}"


# ── real model, real pipeline (download mocked) ───────────────────────────────

@pytest.mark.integration
def test_classify_with_real_model_on_a_test_image() -> None:
    weights = _REPO / "ml" / "vision" / "artifacts" / "weights" / "best.pt"
    images = sorted((_REPO / "ml" / "vision" / "data" / "test" / "images").glob("bat_*.jpg"))
    if not weights.exists() or not images:
        pytest.skip("needs best.pt and the prepared test split")
    data = images[0].read_bytes()
    resp = mock.MagicMock()
    resp.read.return_value = data
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    with mock.patch("ml.vision.safety.urllib.request.urlopen", return_value=resp):
        status, body, _ = call(rest("POST", "/classify", {"image_url": _URL}))
    assert status == 200
    for item in body["items"]:
        assert set(item) == {"label", "confidence", "hazardous", "requires_confirmation"}
        assert item["hazardous"] is (item["label"] in {"ewaste", "battery"})
    assert "battery" in [i["label"] for i in body["items"]]
