# EcoLink Vision ML Pipeline — Evaluation (2026-10-10)

Scope: `ml/` and the ML tests (`test_class_mapping.py`, `test_confidence.py`,
`test_vision_inference.py`, `test_vision_safety.py`, `test_vision_schema.py`).
Environment: Python 3.11.9, torch 2.5.1+cpu, ultralytics 8.3.40 (pinned versions from `requirements.txt`).

## 1. Verdict

| Area | Result |
|------|--------|
| Original 5 test files | **124 / 124 pass** (legacy `run_vision_tests.py`: 76 / 76) |
| Were they sufficient? | **No.** They covered 24% of `ml/`, never ran a real model, and missed the defects below |
| Full suite now | **235 passed, 1 xfail** (documented open SSRF gap) — 8 real-model integration tests included |
| Pipeline end to end | Data → train → evaluate → benchmark → inference → Lambda handler all run |
| Model (test split, 393 images) | mAP50 **0.59**, mAP50-95 **0.43**; image-level macro F1 **0.76**; hazard recall **0.79** at 0.98 precision |
| CPU latency (416 px, i5-1135G7) | ~112 ms mean, 8.9 img/s (`benchmark.py`) |

## 2. Why the original tests were insufficient

| Gap | Consequence | Added coverage |
|-----|-------------|----------------|
| `handler.py` 0% | Lambda status codes, body parsing and import-time warm-load untested | `test_vision_handler.py` (18 tests) |
| `model_loader.py` 28% | Weight resolution, singleton, thread safety untested | `test_vision_model_loader.py` (17) |
| Only fake models with canonical IDs | The COCO-fallback mislabelling bug (below) was invisible | `test_vision_robustness.py` label-mapping tests |
| No corrupt/truncated/odd-mode images | Corrupt uploads returned HTTP 500 | `test_vision_robustness.py` (40) |
| Shallow URL tests | Userinfo, suffix, IPv6, redirect tricks untested | `test_vision_robustness.py` |
| Dataset scripts 0% | COCO parser could not read per-split exports | `test_dataset_scripts.py` (29) |
| No real-model test | Weight/class contract, image size and quality never checked | `test_vision_model_integration.py` (8, `-m integration`) |

Runtime modules are now at 96–100% line coverage. The offline CLIs (`train.py`, `evaluate.py`,
`benchmark.py`, the fetch and autolabel scripts) are validated by running them end to end, not
by unit tests.

## 3. Defects found

| # | Severity | Defect | Status |
|---|----------|--------|--------|
| 1 | **Critical** | Without `best.pt` the pipeline silently loads COCO `yolov8n.pt` and maps class IDs **by position**: a bus became hazardous `ewaste`, a person became `paper` | **Fixed**: labels come from the model's class names via `dataset_mapping.yaml`; unmapped classes are dropped (`inference.py`) |
| 2 | **High** | `dataset.yaml` relative `path:` is resolved by ultralytics against its global `datasets_dir`, so `train.py`/`evaluate.py` could not find the data | **Fixed**: `paths.resolve_dataset_yaml()` |
| 3 | **High** | `inference.yaml` `image_size: 640` vs training size: inferring a 416-trained model at 640 cut recall from 0.44 to 0.25 | **Fixed**: `image_size: 416`, plus an integration test that checks it |
| 4 | High | `fetch_image_bytes` follows HTTP redirects without re-validating the host, so an allowed `*.amazonaws.com` URL can redirect to `169.254.169.254` (SSRF) | **Open**: strict `xfail` test; fix by opening via an `HTTPRedirectHandler` subclass that calls `validate_image_url()`. Hostname-only checks also let `internal-*.elb.amazonaws.com` names resolve to private IPs |
| 5 | Medium | Corrupt/truncated images raised unhandled PIL errors → HTTP 500 | **Fixed**: `InferenceError(400)` |
| 6 | Medium | `train.py` copied `project/name/weights/best.pt`, but ultralytics auto-increments run names, so it copied **stale** weights on re-runs | **Fixed**: uses `model.trainer.save_dir` |
| 7 | Medium | `prepare_dataset.py --format coco` resolved `file_name` against the source root, not the JSON's folder, so standard Roboflow per-split exports found 0 images | **Fixed** (+ `_annotations.coco.json` discovery) |
| 8 | Medium | `evaluate.py` confusion matrix had transposed axes (rows labelled "True" were predictions) and an unlabelled background row/column | **Fixed** |
| 9 | Low | Weights path resolved against the CWD (notebooks/Lambda from another dir fell back to COCO) | **Fixed**: repo-relative resolution |
| 10 | Low | Model loaded before input validation; invalid requests paid for a model load | **Fixed** |
| 11 | Low | `evaluate.py`/`train.py` compute mAP at `conf=0.50`; standard mAP uses `conf=0.001` | Open (documented; both reported here) |
| 12 | Design | Hazardous items need confirmation only below **0.70**, non-hazardous below **0.80**, so a battery at 0.75 skips confirmation that plastic at 0.75 gets | Open: team decision |
| 13 | Design | `requires_confirmation` is computed but never reaches the API response, so the agent cannot use it | Open |
| 14 | Low | `load_model(weights_path=X)` returns the cached model and ignores X once one is loaded; `benchmark.py` "peak memory" uses `tracemalloc`, which does not see torch tensors | Open |

All fixes are small and local, and no original test file was modified. See `git diff` once committed.

## 4. Dataset (built for this evaluation)

`ml/vision/data` was empty. Built from public sources with `scripts/fetch_public_datasets.py` +
`scripts/autolabel_batteries.py` + `prepare_dataset.py`, which exercised all three input formats.

| Split | Images | Objects |
|-------|--------|---------|
| train | 3,129 | 8,442 |
| val | 390 | 1,011 |
| test | 393 | 909 |

Sources: Roboflow garbage set (paper, cardboard, plastic, metal, glass; CC BY 4.0), Open Images V7
(e-waste), Kaggle-mirror battery photos (MIT). **Battery boxes are machine-generated**: 699 of 856
images were labelled, and a 48-image spot check showed about 8% partial boxes. The battery photos
are clean product shots, so battery scores (mAP50 0.96) are optimistic for real WhatsApp photos.
Some "battery" images are power banks.

## 5. Model results (YOLOv8n, 12 epochs @ 416 px, MX330 GPU, 2.8 h)

Detection on the held-out test split (`evaluate.py --imgsz 416 --conf 0.001`):

| Class | P | R | mAP50 | mAP50-95 |
|-------|---|---|-------|----------|
| paper | 0.49 | 0.40 | 0.42 | 0.31 |
| cardboard | 0.54 | 0.63 | 0.66 | 0.50 |
| plastic | 0.58 | 0.30 | 0.40 | 0.23 |
| metal | 0.42 | 0.45 | 0.34 | 0.22 |
| glass | 0.67 | 0.66 | 0.68 | 0.46 |
| ewaste | 0.71 | 0.61 | 0.65 | 0.47 |
| battery | 0.92 | 0.86 | 0.96 | 0.80 |
| **all** | 0.59 | 0.57 | **0.59** | **0.43** |

Pipeline output (image-level, API threshold 0.50; from the notebook):
macro P/R/F1 0.84 / 0.71 / 0.76. HIGH-tier detections are 92% correct and MEDIUM-tier 79%, which
supports the tier design. Hazard detection: precision 0.98, **recall 0.79 (32 of 151 hazardous
images missed)**, which is the main safety risk. The dominant error mode is missed objects (the
background column of `artifacts/confusion_matrix.png`), especially plastic, rather than
class confusion.

## 6. Recommendations

1. Fix the redirect SSRF (#4) before exposing `/classify`.
2. Train longer (val mAP was still rising at epoch 12) and at 640 px on a real GPU; add real-world battery/e-waste photos with human labels.
3. Decide #12/#13 with the agent team. Consider a stricter hazardous threshold, since missing a battery is the costliest error.
4. Run `pytest -m integration` in CI wherever `best.pt` is available.

## 7. Reproduce

```bash
python -m pytest tests/test_class_mapping.py tests/test_confidence.py tests/test_vision_*.py tests/test_dataset_scripts.py
python ml/vision/evaluate.py --imgsz 416 --conf 0.001
python ml/vision/scripts/benchmark.py --imgsz 416
# or open ml/vision/notebooks/vision_pipeline_test.ipynb and Run All
```
