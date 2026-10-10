"""
Tests for ml.vision.model_loader and ml.vision.paths — a fake ``ultralytics``
module is injected, so no weights, GPU or network are needed.
"""
import os
import sys
import threading
import types
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import yaml

from ml.vision import model_loader
from ml.vision.paths import REPO_ROOT, repo_path, resolve_dataset_yaml


class _FakeYOLO:
    instances: list = []

    def __init__(self, weights):
        self.weights = weights
        self.device = None
        _FakeYOLO.instances.append(self)

    def to(self, device):
        self.device = device
        return self


@pytest.fixture(autouse=True)
def fake_ultralytics():
    _FakeYOLO.instances = []
    model_loader.reset_model()
    fake = types.ModuleType("ultralytics")
    fake.YOLO = _FakeYOLO
    with mock.patch.dict(sys.modules, {"ultralytics": fake}):
        yield
    model_loader.reset_model()


# ── device resolution ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("device", ["cpu", "cuda", "cuda:0", "mps"])
def test_resolve_device_explicit_passthrough(device) -> None:
    assert model_loader._resolve_device(device) == device


def test_resolve_device_auto_returns_cpu_or_cuda() -> None:
    assert model_loader._resolve_device("auto") in {"cpu", "cuda"}


def test_resolve_device_auto_without_torch_falls_back_to_cpu() -> None:
    with mock.patch.dict(sys.modules, {"torch": None}):
        assert model_loader._resolve_device("auto") == "cpu"


# ── weights resolution ────────────────────────────────────────────────────────

def test_explicit_existing_weights_are_used(tmp_path) -> None:
    w = tmp_path / "custom.pt"
    w.write_bytes(b"x")
    m = model_loader.load_model(weights_path=str(w), device="cpu")
    assert m.weights == str(w)
    assert m.device == "cpu"


def test_missing_weights_fall_back_to_pretrained(tmp_path) -> None:
    with mock.patch.object(model_loader, "_load_inference_config",
                           return_value={"model": {"weights_path": str(tmp_path / "nope.pt")}}):
        m = model_loader.load_model(weights_path=str(tmp_path / "also_missing.pt"), device="cpu")
    assert m.weights == "yolov8n.pt"


def test_broken_inference_config_falls_back_to_pretrained() -> None:
    with mock.patch.object(model_loader, "_load_inference_config", side_effect=OSError("unreadable")):
        m = model_loader.load_model(device="cpu")
    assert m.weights == "yolov8n.pt"


def test_repo_relative_weights_resolve_from_any_cwd(tmp_path, monkeypatch) -> None:
    """Config paths are repo-relative; loading must not depend on the CWD."""
    rel = Path("ml/vision/artifacts/weights/_test_repo_relative.pt")
    target = REPO_ROOT / rel
    target.write_bytes(b"x")
    try:
        monkeypatch.chdir(tmp_path)
        m = model_loader.load_model(weights_path=rel.as_posix(), device="cpu")
        assert Path(m.weights) == target
    finally:
        target.unlink()


# ── singleton semantics ───────────────────────────────────────────────────────

def test_load_model_is_a_singleton() -> None:
    a = model_loader.load_model(device="cpu")
    b = model_loader.load_model(device="cpu")
    assert a is b
    assert len(_FakeYOLO.instances) == 1


def test_get_model_loads_once_then_caches() -> None:
    a = model_loader.get_model()
    b = model_loader.get_model()
    assert a is b
    assert len(_FakeYOLO.instances) == 1


def test_reset_model_forces_reload() -> None:
    a = model_loader.load_model(device="cpu")
    model_loader.reset_model()
    b = model_loader.load_model(device="cpu")
    assert a is not b


def test_concurrent_first_load_creates_one_model() -> None:
    barrier = threading.Barrier(8)
    got = []

    def worker():
        barrier.wait()
        got.append(model_loader.load_model(device="cpu"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_FakeYOLO.instances) == 1
    assert all(m is got[0] for m in got)


# ── paths helpers ─────────────────────────────────────────────────────────────

def test_repo_path_relative_and_absolute(tmp_path) -> None:
    assert repo_path("ml/vision") == REPO_ROOT / "ml" / "vision"
    assert repo_path(tmp_path) == tmp_path


def test_resolve_dataset_yaml_makes_path_absolute(tmp_path) -> None:
    out = resolve_dataset_yaml("ml/vision/config/dataset.yaml", out_dir=tmp_path)
    cfg = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert Path(cfg["path"]).is_absolute()
    assert Path(cfg["path"]) == REPO_ROOT / "ml" / "vision" / "data"
    assert cfg["names"][5] == "ewaste" and cfg["nc"] == 7


def test_resolve_dataset_yaml_keeps_absolute_yaml_untouched(tmp_path) -> None:
    src = tmp_path / "abs.yaml"
    src.write_text(yaml.safe_dump({"path": str(tmp_path), "train": "t", "val": "v", "nc": 1, "names": {0: "a"}}))
    assert resolve_dataset_yaml(src, out_dir=tmp_path / "out") == src
