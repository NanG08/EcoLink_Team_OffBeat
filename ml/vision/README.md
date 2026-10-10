# EcoLink Vision ML Pipeline

## 1. Overview

This pipeline provides a production-ready, CPU-first object detection system for EcoLink waste classification. It uses a fine-tuned YOLOv8n model to detect and classify up to 7 categories of waste in a single WhatsApp photograph, with per-object confidence scores and deterministic hazardous-waste tagging.

It runs **alongside** (not replacing) the existing Bedrock-based classifier in `backend/ecolink/core/classifier.py`. Both implement the same public API contract:

```json
{"items": [{"label": "plastic", "confidence": 0.96, "hazardous": false}]}
```

Architecture:

```
WhatsApp image
      │
      ▼
preprocess
(HTTPS fetch, 5 MB cap, RGB, format check)
      │
      ▼
YOLOv8n inference
(7-class detector, per-object bbox + confidence)
      │
      ▼
safety rules
(confidence filter ≥ 0.50, hazardous tagging,
 deduplication, confirmation flags)
      │
      ▼
structured API response
{"items": [{"label":…,"confidence":…,"hazardous":…}]}
      │
      ▼
Strands Agent
```

---

## 2. Directory Layout

```
ml/vision/
├── config/
│   ├── classes.yaml          # Canonical 7 classes + stable IDs (source of truth)
│   ├── dataset_mapping.yaml  # Source dataset → EcoLink class name mappings
│   ├── inference.yaml        # Runtime thresholds, NMS params, device
│   └── dataset.yaml          # YOLO-compatible dataset descriptor for training
│
├── data/
│   ├── raw/                  # Drop raw annotated datasets here
│   ├── processed/            # dataset_report.json written here
│   ├── train/{images,labels}/
│   ├── val/{images,labels}/
│   └── test/{images,labels}/
│
├── artifacts/
│   ├── weights/
│   │   └── best.pt           # Trained model weights (written by train.py)
│   ├── metrics/
│   │   ├── per_class.json
│   │   └── overall.json
│   ├── confusion_matrix.png
│   ├── benchmark.json
│   └── dataset_stats.json
│
├── scripts/
│   ├── prepare_dataset.py    # Raw → YOLO train/val/test splits
│   ├── validate_dataset.py   # Validate split integrity
│   └── benchmark.py          # CPU inference latency benchmark
│
├── schemas.py       # Dataclasses: Detection, ClassifyResponse, ClassesConfig
├── safety.py        # URL validation, confidence logic, hazardous rules
├── model_loader.py  # Thread-safe singleton YOLO loader
├── inference.py     # Core pipeline: bytes/URL → ClassifyResponse
├── handler.py       # Lambda + direct-callable entry point
├── train.py         # Fine-tune YOLOv8n on EcoLink dataset
├── evaluate.py      # Evaluate trained model on test split
├── requirements.txt
└── README.md        # This file
```

---

## 3. Installation

```bash
# CPU-only PyTorch (smaller download):
pip install torch==2.5.1 torchvision==0.20.1 \
    --index-url https://download.pytorch.org/whl/cpu

# Then install the rest:
pip install -r ml/vision/requirements.txt
```

---

## 4. Quick Start — Inference

```python
from ml.vision.handler import classify

result = classify({"image_url": "https://mybucket.s3.amazonaws.com/img.jpg"})
# {"items": [{"label": "plastic", "confidence": 0.88, "hazardous": false}, ...]}
```

On first run, ultralytics downloads the pretrained `yolov8n.pt` weights automatically (~6 MB). After fine-tuning, `ml/vision/artifacts/weights/best.pt` is used instead.

The pretrained fallback is a COCO model, so its class IDs mean something else (ID 5 is "bus").
Inference therefore maps every detection through the model's own class names and
`dataset_mapping.yaml`. Under the fallback, only COCO classes with an EcoLink equivalent are
returned (e.g. cell phone → ewaste, bottle → plastic, book → paper), and the rest are dropped.

---

## 5. Class Configuration — 7 Classes

| ID | Label     | Hazardous |
|----|-----------|-----------|
|  0 | paper     | No        |
|  1 | cardboard | No        |
|  2 | plastic   | No        |
|  3 | metal     | No        |
|  4 | glass     | No        |
|  5 | ewaste    | **Yes**   |
|  6 | battery   | **Yes**   |

Class IDs are frozen. Do not renumber them after training.

---

## 6. Confidence Thresholds

| Level  | Threshold | Behaviour                                              |
|--------|-----------|--------------------------------------------------------|
| HIGH   | ≥ 0.80    | Included in response; no extra confirmation            |
| MEDIUM | ≥ 0.50    | Included in response; non-hazardous may prompt confirm |
| Below  | < 0.50    | Filtered out; not returned in API response             |

Hazardous classes (ewaste, battery) additionally require explicit user confirmation when confidence is below **0.70**.

---

## 7. Hazardous Waste Rules

- `ewaste` and `battery` are always tagged `"hazardous": true` in the response.
- These items are routed to authorised drop-off points, never to a normal pickup.
- When a hazardous item is detected with confidence < 0.70, `requires_confirmation=True` is set internally (not exposed in the public API but used by the agent).

---

## 8. Dataset Pipeline

### Fetch public data (optional)

To build a 7-class dataset from public sources (~1 GB download):

```bash
python ml/vision/scripts/fetch_public_datasets.py --all --max-garbage 3000
python ml/vision/scripts/autolabel_batteries.py            # boxes for classification-only battery photos
python ml/vision/scripts/prepare_dataset.py --source-dir ml/vision/data/raw/garbage_coco      --format coco
python ml/vision/scripts/prepare_dataset.py --source-dir ml/vision/data/raw/openimages_ewaste --format yolo
python ml/vision/scripts/prepare_dataset.py --source-dir ml/vision/data/raw/battery_images    --format pascal-voc
python ml/vision/scripts/validate_dataset.py
```

| Source | EcoLink classes | Labels |
|--------|-----------------|--------|
| [keremberke/garbage-object-detection](https://huggingface.co/datasets/keremberke/garbage-object-detection) (CC BY 4.0) | paper, cardboard, plastic, metal, glass | human (COCO) |
| Open Images V7 validation + test boxes | ewaste | human |
| [khoaliamle/Garbage_Classification_YOLO](https://huggingface.co/datasets/khoaliamle/Garbage_Classification_YOLO) `battery` (MIT) | battery | **machine-generated** (background subtraction + YOLO-World; needs `pip install ftfy regex` and `pip install --no-deps git+https://github.com/ultralytics/CLIP.git`) |

Battery photos are mostly studio product shots, so battery metrics are optimistic for real
WhatsApp photos. Some upstream "battery" images are power banks.

### Directory structure

Place raw images and annotations in `ml/vision/data/raw/`. The `prepare_dataset.py` script processes them into YOLO-format train/val/test splits.

YOLO annotation format (one `.txt` file per image):
```
<class_id> <x_center> <y_center> <width> <height>
```
All coordinates are normalised to [0, 1].

### Prepare dataset

```bash
# YOLO-format source data:
python ml/vision/scripts/prepare_dataset.py \
    --source-dir ml/vision/data/raw \
    --output-dir ml/vision/data \
    --format yolo \
    --split 0.8 0.1 0.1 \
    --seed 42

# COCO-format source data:
python ml/vision/scripts/prepare_dataset.py \
    --source-dir path/to/coco/dataset \
    --format coco

# Pascal VOC XML:
python ml/vision/scripts/prepare_dataset.py \
    --source-dir path/to/voc/dataset \
    --format pascal-voc
```

### Validate dataset

```bash
python ml/vision/scripts/validate_dataset.py --data-dir ml/vision/data
```

---

## 9. Training

```bash
python ml/vision/train.py \
    --epochs 100 \
    --batch-size 16 \
    --imgsz 640 \
    --lr0 0.01 \
    --device auto \
    --seed 42 \
    --patience 10 \
    --weights yolov8n.pt \
    --data ml/vision/config/dataset.yaml
```

CPU-safe defaults: `batch-size=16`, `epochs=100`. Use `--batch-size 4` on machines with limited RAM.

To resume an interrupted training run:
```bash
python ml/vision/train.py --resume
```

Best weights are automatically copied to `ml/vision/artifacts/weights/best.pt`.

`model.image_size` in `inference.yaml` **must equal** the `--imgsz` used for training. The
shipped `best.pt` was trained at 416; inferring it at 640 roughly halved recall.
`tests/test_vision_model_integration.py` checks this against the checkpoint.

---

## 10. Evaluation

```bash
python ml/vision/evaluate.py \
    --weights ml/vision/artifacts/weights/best.pt \
    --data ml/vision/config/dataset.yaml \
    --split test \
    --conf 0.50
```

Outputs:
- Per-class precision/recall/mAP50/mAP50-95 table (stdout)
- `ml/vision/artifacts/confusion_matrix.png`
- `ml/vision/artifacts/metrics/per_class.json`
- `ml/vision/artifacts/metrics/overall.json`

---

## 11. Inference API Contract

**Request** (POST `/classify`):
```json
{"image_url": "https://mybucket.s3.amazonaws.com/waste.jpg"}
```

**Response**:
```json
{
  "items": [
    {"label": "plastic", "confidence": 0.96, "hazardous": false},
    {"label": "battery", "confidence": 0.91, "hazardous": true}
  ]
}
```

- `label`: one of the 7 canonical class names
- `confidence`: float rounded to 2 decimal places
- `hazardous`: boolean (deterministic, not confidence-dependent)
- Items sorted by confidence descending
- Items below 0.50 confidence are omitted

---

## 12. Class Mapping (`dataset_mapping.yaml`)

When adding a new source dataset, map its class names to EcoLink canonicals:

```yaml
mappings:
  plastic_bottle: plastic
  smartphone:     ewaste
  battery:        battery
  food:           null      # null = discard this class
```

Any class not listed (and not a canonical name itself) is silently discarded during dataset preparation.

---

## 13. Environment Variables

| Variable              | Default                             | Description                  |
|-----------------------|-------------------------------------|------------------------------|
| `ECOLINK_WEIGHTS`     | `ml/vision/artifacts/weights/best.pt` | Override weights path        |
| `ECOLINK_DEVICE`      | `auto`                              | `cpu`, `cuda`, or `auto`     |
| `ECOLINK_CONF_THRESH` | `0.50`                              | Override detection threshold |

These are not currently read automatically; they are placeholders for future Lambda environment configuration.

---

## 14. Lambda Deployment Notes

The handler pre-loads the model at module import time for warm container reuse:

```python
# ml/vision/handler.py
_MODEL = load_model(weights_path=..., device="cpu")
```

Lambda layer recommended contents:
- `torch` (CPU-only wheels)
- `ultralytics`
- `Pillow`
- `PyYAML`

Model weights (`best.pt`, ~6 MB) can be packaged in the layer or loaded from S3 on cold-start.

---

## 15. CPU vs GPU

The pipeline is designed for CPU-first operation:
- Default device: `auto` (falls back to CPU if CUDA unavailable)
- YOLOv8n nano model: optimised for speed and low memory
- Typical CPU latency: 200–600 ms per image (depends on hardware)
- No CUDA required at any stage

GPU training is supported automatically when a CUDA device is available; the same training script runs on both.

---

## 16. Running Tests

```bash
pytest tests/test_vision_schema.py \
       tests/test_vision_safety.py \
       tests/test_vision_inference.py \
       tests/test_confidence.py \
       tests/test_class_mapping.py \
       tests/test_vision_handler.py \
       tests/test_vision_model_loader.py \
       tests/test_vision_robustness.py \
       tests/test_dataset_scripts.py \
       tests/test_vision_model_integration.py \
       -v
```

All tests except `test_vision_model_integration.py` run without model weights, GPU, or network
access. The integration tests (`pytest -m integration`) use the real `best.pt` and the prepared
test split and skip automatically when either is missing.

`test_vision_robustness.py::test_redirect_to_metadata_endpoint_is_not_followed` is a strict
`xfail` documenting an open SSRF gap: `fetch_image_bytes` follows HTTP redirects without
re-validating the target host.

## 16a. Test notebook

`ml/vision/notebooks/vision_pipeline_test.ipynb` dry-runs the whole pipeline on the prepared
dataset (select the `.venv` kernel, then *Run All*). It prints JSON for per-image confidence
scores, per-class classification metrics, per-class mAP, API-contract checks and latency, and saves
everything to `ml/vision/artifacts/notebook_report.json`.

---

## 17. Troubleshooting

**`ModuleNotFoundError: No module named 'ultralytics'`**
Run: `pip install -r ml/vision/requirements.txt`

**`FileNotFoundError: best.pt not found`**
Train the model first: `python ml/vision/train.py`
Or let the pipeline use the default pretrained YOLOv8n weights (no fine-tuning).

**`InferenceError: image_url must use HTTPS`**
All image URLs must be HTTPS pre-signed S3 URLs (`*.amazonaws.com`).

**`InferenceError: Image exceeds 5 MB limit`**
Compress the image before uploading, or increase `max_image_bytes` in `inference.yaml`.

**`ValueError: Confidence X is below detection threshold`**
The confidence threshold is 0.50. Detections below this are filtered. Adjust `detection_threshold` in `inference.yaml` if needed.

**Out-of-memory on CPU training**
Reduce `--batch-size` to 4 or 8: `python ml/vision/train.py --batch-size 4`

**YAML key errors in tests**
Ensure `classes.yaml` uses `hazardous_classes` key (not `hazardous_labels`). The `load_classes_config()` function handles both for backward compatibility.
