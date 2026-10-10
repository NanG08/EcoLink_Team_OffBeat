# ML Vision Pipeline — Implementation Plan

## Context

This plan targets the creation of a complete YOLOv8n-based waste detection and classification
pipeline inside the existing EcoLink project. The pipeline sits **alongside** (not replacing)
the existing Bedrock-based classifier in `backend/ecolink/core/classifier.py`; it provides a
local, CPU-friendly inference alternative that is called via `ml/vision/handler.py`.

**Do not modify** these existing files:
- `backend/ecolink/core/constants.py`
- `backend/ecolink/core/classifier.py`
- `backend/ecolink/handlers/proxy.py`
- `agent/tools.py`

**Public API contract** (unchanged):
```json
{"items": [{"label": "plastic", "confidence": 0.96, "hazardous": false}]}
```

**Implementation order**: config → schemas → safety → model_loader → inference → handler →
dataset scripts → training → evaluation → benchmark → tests → docs

---

## Implementation Plan

- [ ] 1. Create data directory tree with `.gitkeep` placeholder files.

  All downstream scripts and tests assume these directories exist.  Create
  them as empty directories tracked by git via `.gitkeep` files.  No Python
  code yet; this is purely filesystem scaffolding.

  Files to create:
  - `ml/vision/data/raw/.gitkeep`
  - `ml/vision/data/processed/.gitkeep`
  - `ml/vision/data/train/images/.gitkeep`
  - `ml/vision/data/train/labels/.gitkeep`
  - `ml/vision/data/val/images/.gitkeep`
  - `ml/vision/data/val/labels/.gitkeep`
  - `ml/vision/data/test/images/.gitkeep`
  - `ml/vision/data/test/labels/.gitkeep`
  - `ml/vision/artifacts/weights/.gitkeep`
  - `ml/vision/artifacts/metrics/.gitkeep`

  Verify: `python -c "import pathlib; assert all(pathlib.Path(p).parent.is_dir() for p in ['ml/vision/data/train/images/.gitkeep']); print('OK')"` — prints `OK`.

---

- [ ] 2. Write the YAML configuration files.

  **`ml/vision/config/classes.yaml`** — single source of truth for class IDs and the
  hazardous set.  Must contain:
  - `class_names` list in stable order `[paper, cardboard, plastic, metal, glass, ewaste, battery]`
    (indices 0–6 are the canonical class IDs).
  - `hazardous_labels` list `[ewaste, battery]`.
  - `confidence_thresholds` map: `high: 0.80`, `medium: 0.50`, `discard: 0.0`.

  **`ml/vision/config/dataset_mapping.yaml`** — maps every known source-dataset class name to
  one of the seven canonical EcoLink labels (or `null` to discard).  Must cover at minimum:
  `plastic_bottle`, `plastic_bag`, `glass_bottle`, `drink_can`, `paper`, `cardboard`,
  `battery`, `smartphone`, `laptop`, `electronic_device`, `monitor`, `keyboard`, `mouse`,
  `cable`, `metal_can`, `aluminium_can`, `newspaper`, `magazine`, `trash`, `garbage`.
  Organise as a flat YAML map `source_class: canonical_label` where a `null` value means
  "discard this class".

  **`ml/vision/config/inference.yaml`** — runtime inference parameters:
  - `confidence_threshold: 0.50`  (minimum to include a detection)
  - `iou_threshold: 0.45`
  - `max_detections: 20`
  - `image_size: 640`
  - `model_path: "ml/vision/artifacts/weights/best.pt"`
  - `device: "auto"`  (`"auto"` → detect cuda, fall back to cpu at runtime)

  **`ml/vision/config/dataset.yaml`** — YOLO-compatible dataset descriptor read by
  `ultralytics` during training.  Must contain:
  - `path` pointing to `ml/vision/data` (use a relative path that works from the repo root)
  - `train`, `val`, `test` keys pointing to `train/images`, `val/images`, `test/images`
  - `nc: 7`
  - `names` list matching `classes.yaml`

  Files:
  - `ml/vision/config/classes.yaml`
  - `ml/vision/config/dataset_mapping.yaml`
  - `ml/vision/config/inference.yaml`
  - `ml/vision/config/dataset.yaml`

  Verify: `python -c "import yaml; c=yaml.safe_load(open('ml/vision/config/classes.yaml')); assert c['class_names'][5]=='ewaste'; print('OK')"` — prints `OK`.

---

- [ ] 3. Create `ml/vision/__init__.py` — package marker and version constant.

  This is a minimal file that:
  - Defines `__version__ = "0.1.0"`.
  - Exposes nothing else (keeps the public surface minimal).
  - Does NOT import heavy dependencies (ultralytics, torch) at module level so that
    `import ml.vision` is safe in test environments without GPU/model weights.

  File: `ml/vision/__init__.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.');  from ml.vision import __version__; print(__version__)"` — prints `0.1.0` without error.

---

- [ ] 4. Implement `ml/vision/schemas.py` — shared dataclass schemas and config loader.

  This module is imported by every other module in the pipeline; it must have zero
  heavy dependencies (stdlib + PyYAML only).

  **Config loader** — a function `load_classes_config(path: Path | None = None) -> ClassesConfig`
  that reads `ml/vision/config/classes.yaml` (default) via `yaml.safe_load` and returns a
  `ClassesConfig` dataclass.  Uses `pathlib.Path` throughout.  Caches the result after first
  load (module-level singleton dict keyed on resolved path).

  **Dataclasses to define**:

  ```python
  @dataclass
  class ClassesConfig:
      class_names: list[str]          # ["paper", ..., "battery"]
      hazardous_labels: frozenset[str]
      confidence_thresholds: dict[str, float]  # {"high":0.80, "medium":0.50, "discard":0.0}
      # Derived helpers:
      def id_for(self, label: str) -> int: ...   # raises KeyError if unknown
      def label_for(self, id: int) -> str: ...   # raises IndexError if out of range
      def is_hazardous(self, label: str) -> bool: ...

  @dataclass
  class Detection:
      label: str
      confidence: float          # already rounded to 4 dp
      hazardous: bool
      bbox: tuple[float,float,float,float]  # (x_center, y_center, w, h) normalised
      class_id: int

  @dataclass
  class ClassifyResponse:
      items: list[dict]   # [{"label":str,"confidence":float,"hazardous":bool}]

      def to_api_dict(self) -> dict:  # returns {"items": [...]}
  ```

  **Confidence tier helper** — a standalone function `confidence_tier(score: float, cfg: ClassesConfig) -> str`
  returning `"HIGH"`, `"MEDIUM"`, or `"DISCARD"` based on the thresholds in `cfg`.

  File: `ml/vision/schemas.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.'); from ml.vision.schemas import load_classes_config, Detection; cfg=load_classes_config(); assert cfg.is_hazardous('battery'); print('OK')"` — prints `OK`.

---

- [ ] 5. Implement `ml/vision/safety.py` — URL validation and image fetch guard.

  This module centralises all SSRF-prevention logic for the vision pipeline, mirroring the
  same security model as `backend/ecolink/core/classifier.py` (HTTPS-only, `.amazonaws.com`
  allow-list) but as a standalone module with no dependency on the `ecolink` package.

  **Functions to implement**:

  ```python
  def is_allowed_host(hostname: str, extra_hosts: frozenset[str] = frozenset()) -> bool:
      """Return True only for hostnames ending in .amazonaws.com or in extra_hosts."""

  def validate_image_url(url: str, extra_hosts: frozenset[str] = frozenset()) -> None:
      """Raise ValueError with a descriptive message if the URL fails any check:
      - scheme must be https
      - hostname must pass is_allowed_host
      - must not be localhost / 169.254.x.x (link-local) / 10.x / 172.16–31.x / 192.168.x
      """

  def fetch_image_bytes(
      url: str,
      max_bytes: int = 5 * 1024 * 1024,
      timeout: int = 10,
      extra_hosts: frozenset[str] = frozenset(),
  ) -> bytes:
      """Fetch the image after calling validate_image_url, cap at max_bytes.
      Raises ValueError on URL violations, urllib.error.URLError on network errors,
      ValueError if the download exceeds max_bytes.
      """
  ```

  Use only `urllib.request`, `urllib.parse`, `urllib.error`, `ipaddress` from stdlib.
  Do NOT import from `ecolink.*`.

  File: `ml/vision/safety.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.'); from ml.vision.safety import validate_image_url; validate_image_url('https://bucket.s3.amazonaws.com/img.jpg'); print('OK')"` — prints `OK`.

---

- [ ] 6. Implement `ml/vision/model_loader.py` — singleton YOLOv8n model loader.

  This module owns the single in-process model instance.  It must tolerate being imported
  in environments where model weights do not yet exist (raises a clear `FileNotFoundError`
  only when `.get_model()` is actually called, not on import).

  **Design decisions**:
  - Use `ultralytics.YOLO` as the model class.
  - Resolve `device` from `inference.yaml`: `"auto"` → `"cuda"` if `torch.cuda.is_available()`
    else `"cpu"`.  Never hard-code the device.
  - Use `threading.Lock` to protect the singleton during concurrent Lambda warm-starts.
  - Expose a `reset_model()` function (no-op lock-protected reset for tests).

  ```python
  _lock: threading.Lock = threading.Lock()
  _model: YOLO | None = None

  def get_model(config_path: Path | None = None) -> YOLO:
      """Load and return the singleton YOLO model.
      Thread-safe. Raises FileNotFoundError if weights do not exist.
      """

  def reset_model() -> None:
      """Clear the singleton (used in tests)."""
  ```

  Load `inference.yaml` via `yaml.safe_load` to read `model_path` and `device`.
  `model_path` is resolved relative to the project root (the directory containing `ml/`).

  File: `ml/vision/model_loader.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.'); from ml.vision import model_loader; model_loader.reset_model(); print('OK')"` — prints `OK` (import-only, no weights needed).

---

- [ ] 7. Implement `ml/vision/inference.py` — core inference pipeline.

  This module converts a raw image (bytes or URL) into a `ClassifyResponse`, applying the
  confidence filtering, label mapping, hazardous tagging, and deduplication logic.

  **Functions to implement**:

  ```python
  def run_inference(
      image_input: bytes | str,          # bytes = raw image; str = HTTPS URL
      cfg: ClassesConfig | None = None,  # loads default if None
      model: YOLO | None = None,         # uses singleton if None
      infer_cfg: dict | None = None,     # loads inference.yaml if None
  ) -> ClassifyResponse:
      """
      1. If image_input is str, call safety.fetch_image_bytes(url).
      2. Write bytes to a NamedTemporaryFile (ultralytics needs a file path or numpy array).
      3. Run model.predict(...) with conf=confidence_threshold, iou=iou_threshold,
         imgsz=image_size, device=device, max_det=max_detections.
      4. Iterate detections from result.boxes:
         a. Map class_id → label via cfg.label_for(int(cls)).
         b. Discard if confidence < cfg.confidence_thresholds["discard"] (which equals
            inference.yaml confidence_threshold effectively).
         c. For duplicate labels keep the highest-confidence detection.
      5. Sort descending by confidence.
      6. Build ClassifyResponse with items as [{"label","confidence","hazardous"}].
      7. Clean up the temp file.
      """
  ```

  **Class-ID to label mapping**: use `ClassesConfig.label_for(id)` — this is the *only*
  place that translates YOLO numeric class IDs to public label strings.  Never hard-code
  label strings in this file.

  Dependency chain: `inference.py` imports from `schemas.py`, `safety.py`, `model_loader.py`.
  It does NOT import from `ecolink.*`.

  File: `ml/vision/inference.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.'); from ml.vision import inference; print('OK')"` — prints `OK` (import-only, no weights needed).

---

- [ ] 8. Implement `ml/vision/handler.py` — drop-in HTTP-callable entry point.

  This is the thin public entry point that maps an incoming request dict (compatible with
  the shape used by `backend/ecolink/handlers/proxy.py`) to a `ClassifyResponse` dict.
  Its contract must be identical to the existing `/classify` API: it accepts
  `{"image_url": str}` and returns `{"items": [...]}`.

  ```python
  def classify(request: dict) -> dict:
      """
      Extract "image_url" from request.
      Call inference.run_inference(image_url).
      Return response.to_api_dict().
      Raise ValueError for missing/invalid image_url (caller maps to HTTP 400).
      """
  ```

  This handler does NOT call `safety.validate_image_url` directly — that is called inside
  `inference.run_inference` → `safety.fetch_image_bytes`.  The handler's job is request
  parsing and response serialisation only.

  File: `ml/vision/handler.py`

  Verify: `python -c "import sys; sys.path.insert(0,'.'); from ml.vision.handler import classify; print('OK')"` — prints `OK`.

---

- [ ] 9. Implement `ml/vision/scripts/prepare_dataset.py` — dataset normalisation script.

  This is a standalone CLI script (invoked as `python ml/vision/scripts/prepare_dataset.py`).
  It reads raw annotations, maps source classes to EcoLink classes, converts to YOLO format,
  and writes the train/val/test split.

  **Responsibilities**:
  1. Accept CLI arguments via `argparse`:
     - `--raw-dir` (default `ml/vision/data/raw`)
     - `--out-dir` (default `ml/vision/data`)
     - `--mapping` (default `ml/vision/config/dataset_mapping.yaml`)
     - `--classes` (default `ml/vision/config/classes.yaml`)
     - `--train-ratio` (default `0.8`)
     - `--val-ratio` (default `0.1`)
     - `--seed` (default `42`) — for deterministic shuffle
  2. Discover all image files (`.jpg`, `.jpeg`, `.png`) in `--raw-dir` (recursively).
  3. For each image, look for a paired label file (same stem, `.txt` extension, or a
     sub-directory named `labels/` at the same level as the `images/` directory).
  4. Parse each YOLO-format annotation line: `<class_id_or_name> cx cy w h`.
  5. Apply `dataset_mapping.yaml`: translate source class name/id → canonical label; skip
     lines whose source class maps to `null`.
  6. Validate bounding boxes: `0 < cx, cy, w, h <= 1`; log and skip malformed lines.
  7. Detect missing images (label file exists but no image); log warnings.
  8. Detect duplicates by SHA-256 hash of image bytes; emit a warning, keep the first.
  9. Shuffle image list with `random.seed(seed)` then split at `train_ratio` /
     `(train_ratio + val_ratio)` boundaries.
  10. Write output annotations to `out-dir/{train,val,test}/labels/` and copy images to
      `out-dir/{train,val,test}/images/`.
  11. Write a JSON statistics report to `out-dir/processed/dataset_report.json` containing:
      `total_images`, `total_objects`, `objects_per_class` (dict), `images_per_class` (dict),
      `train_objects_per_class`, `val_objects_per_class`, `test_objects_per_class`.

  Use only stdlib (`pathlib`, `hashlib`, `shutil`, `json`, `random`, `argparse`, `logging`)
  plus PyYAML.  No torch or ultralytics dependency.

  File: `ml/vision/scripts/prepare_dataset.py`

  Verify: `python ml/vision/scripts/prepare_dataset.py --help` — exits 0 and prints usage.

---

- [ ] 10. Implement `ml/vision/scripts/validate_dataset.py` — post-split dataset validator.

  A standalone CLI script that runs after `prepare_dataset.py` and checks the output is
  consistent and training-ready.

  **Checks to perform**:
  1. Every label file in `{train,val,test}/labels/` has a corresponding image in `{split}/images/`.
  2. Every image has at least one annotation.
  3. All class IDs in annotation files are in range `[0, nc-1]` where `nc=7`.
  4. All bounding box values are normalised `(0, 1]`.
  5. No image appears in more than one split (check by filename stem).
  6. Class distribution in train split has no class with zero samples (emits a WARNING,
     not an error, since the dataset may be incomplete during development).
  7. Print a compact per-split table: `split | images | objects | classes_present`.

  CLI argument: `--data-dir` (default `ml/vision/data`).

  File: `ml/vision/scripts/validate_dataset.py`

  Verify: `python ml/vision/scripts/validate_dataset.py --help` — exits 0 and prints usage.

---

- [ ] 11. Implement `ml/vision/train.py` — YOLOv8n fine-tuning entry point.

  A standalone CLI script that fine-tunes pretrained YOLOv8n weights on the EcoLink dataset.

  **CLI arguments** (all with sensible CPU-safe defaults):
  - `--epochs` (default `50`)
  - `--batch` (default `8`) — small batch for CPU
  - `--imgsz` (default `640`)
  - `--lr0` (default `0.01`) — initial learning rate
  - `--conf` (default `0.50`) — confidence threshold during validation
  - `--iou` (default `0.45`)
  - `--seed` (default `42`)
  - `--patience` (default `10`) — early-stopping patience (epochs without mAP improvement)
  - `--resume` (flag) — pass `resume=True` to ultralytics to resume from last checkpoint
  - `--weights` (default `"yolov8n.pt"`) — starting weights; ultralytics auto-downloads
  - `--data` (default `"ml/vision/config/dataset.yaml"`)
  - `--project` (default `"ml/vision/artifacts"`)
  - `--name` (default `"train"`)

  **Implementation**:
  1. Detect device: `"cuda" if torch.cuda.is_available() else "cpu"`.
  2. Load `ultralytics.YOLO(weights)`.
  3. Call `model.train(data=data, epochs=epochs, batch=batch, imgsz=imgsz, lr0=lr0,
     device=device, patience=patience, seed=seed, resume=resume, project=project, name=name)`.
  4. After training, copy `{project}/{name}/weights/best.pt` to
     `ml/vision/artifacts/weights/best.pt` so `inference.yaml`'s default path resolves.
  5. Print the final mAP50 and mAP50-95 from `results.box.map50` and `results.box.map`.

  File: `ml/vision/train.py`

  Verify: `python ml/vision/train.py --help` — exits 0 and prints usage without importing
  GPU-dependent code (ultralytics lazy-imports torch only on actual train call).

---

- [ ] 12. Implement `ml/vision/evaluate.py` — post-training evaluation script.

  A standalone CLI script that runs the trained model on the test split and reports metrics.

  **CLI arguments**:
  - `--weights` (default `"ml/vision/artifacts/weights/best.pt"`)
  - `--data` (default `"ml/vision/config/dataset.yaml"`)
  - `--imgsz` (default `640`)
  - `--conf` (default `0.50`)
  - `--iou` (default `0.45`)
  - `--split` (default `"test"`) — `"test"` | `"val"`
  - `--out` (default `"ml/vision/artifacts/metrics/eval_report.json"`)

  **Implementation**:
  1. Load `ultralytics.YOLO(weights)`.
  2. Detect device automatically.
  3. Call `model.val(data=data, split=split, imgsz=imgsz, conf=conf, iou=iou, device=device)`.
  4. Extract per-class AP from `metrics.ap_class_index` and `metrics.ap`.
  5. Build a report dict: `{"mAP50": float, "mAP50_95": float, "per_class": {"paper": {...}, ...}}`.
  6. Write report to `--out` as JSON.
  7. Print the report table to stdout.

  File: `ml/vision/evaluate.py`

  Verify: `python ml/vision/evaluate.py --help` — exits 0 and prints usage.

---

- [ ] 13. Implement `ml/vision/scripts/benchmark.py` — CPU inference latency benchmark.

  A standalone CLI script that measures per-image inference latency on CPU without requiring
  real images (uses random noise tensors for speed measurement).

  **CLI arguments**:
  - `--weights` (default `"ml/vision/artifacts/weights/best.pt"`)
  - `--imgsz` (default `640`)
  - `--iterations` (default `20`)
  - `--warmup` (default `3`)

  **Implementation**:
  1. Load YOLO model on CPU.
  2. Generate `warmup` random 3×imgsz×imgsz numpy uint8 arrays; run predict (discard output).
  3. Time `iterations` predict calls using `time.perf_counter`.
  4. Report: mean latency ms, p50, p95, p99 (use `statistics.quantiles`), min, max.
  5. Print a human-readable table and write JSON to stdout.

  File: `ml/vision/scripts/benchmark.py`

  Verify: `python ml/vision/scripts/benchmark.py --help` — exits 0 and prints usage.

---

- [ ] 14. Write `tests/test_vision_schema.py` — unit tests for `schemas.py`.

  Tests must work without model weights, torch, or ultralytics — pure Python.

  **Test cases**:
  - `test_class_names_order`: assert `cfg.class_names` equals the canonical 7-element list
    in the specified order (IDs 0–6 match exactly).
  - `test_id_for_label`: assert `cfg.id_for("ewaste") == 5`, `cfg.id_for("battery") == 6`.
  - `test_label_for_id`: assert `cfg.label_for(0) == "paper"`, round-trip for all 7.
  - `test_unknown_label_raises`: assert `KeyError` from `cfg.id_for("gold")`.
  - `test_hazardous_set`: assert `cfg.is_hazardous("ewaste")` and `cfg.is_hazardous("battery")`
    are `True`; assert `cfg.is_hazardous("paper")` is `False`.
  - `test_confidence_tier_high`: score 0.90 → `"HIGH"`.
  - `test_confidence_tier_medium`: score 0.65 → `"MEDIUM"`.
  - `test_confidence_tier_discard`: score 0.40 → `"DISCARD"`.
  - `test_confidence_tier_boundary`: score exactly 0.80 → `"HIGH"`, 0.50 → `"MEDIUM"`.
  - `test_classify_response_to_api_dict`: build a `ClassifyResponse`, call `to_api_dict()`,
    assert it has key `"items"` and each item has `"label"`, `"confidence"`, `"hazardous"`.

  File: `tests/test_vision_schema.py`

  Dependencies: `schemas.py` (item 4), `classes.yaml` (item 2). No torch/ultralytics.

  Verify: `pytest tests/test_vision_schema.py -v` — all tests pass.

---

- [ ] 15. Write `tests/test_vision_safety.py` — unit tests for `safety.py`.

  Tests must work without network access (patch `urllib.request.urlopen`).

  **Test cases**:
  - `test_allowed_amazonaws_subdomain`: `is_allowed_host("bucket.s3.amazonaws.com")` → `True`.
  - `test_allowed_amazonaws_root`: `is_allowed_host("s3.amazonaws.com")` → `True`.
  - `test_blocked_non_aws`: `is_allowed_host("evil.example.com")` → `False`.
  - `test_blocked_localhost`: `validate_image_url("https://localhost/x.jpg")` → `ValueError`.
  - `test_blocked_link_local`: `validate_image_url("https://169.254.169.254/latest/meta")` → `ValueError`.
  - `test_blocked_http_scheme`: `validate_image_url("http://bucket.s3.amazonaws.com/x")` → `ValueError`.
  - `test_blocked_private_10`: `validate_image_url("https://10.0.0.1/x.jpg")` → `ValueError`.
  - `test_valid_presigned_url_passes`: `validate_image_url("https://mybucket.s3.amazonaws.com/images/foo.jpg")` → no exception.
  - `test_fetch_caps_at_max_bytes`: mock `urlopen` to return 6 MB; assert `ValueError` raised by
    `fetch_image_bytes(url, max_bytes=5*1024*1024)`.
  - `test_extra_hosts_allow_list`: pass `extra_hosts=frozenset({"cdn.myapp.com"})`;
    `is_allowed_host("cdn.myapp.com", ...)` → `True`.

  File: `tests/test_vision_safety.py`

  Dependencies: `safety.py` (item 5). Stdlib only.

  Verify: `pytest tests/test_vision_safety.py -v` — all tests pass.

---

- [ ] 16. Write `tests/test_vision_inference.py` — unit tests for the inference pipeline.

  These tests mock the YOLO model to avoid requiring weights.

  **Setup**: define a `FakeModel` class whose `predict()` method returns a list containing one
  fake result object.  The fake result has a `boxes` attribute with `.cls`, `.conf`, and
  `.xyxyn` tensors mimicking the ultralytics output shape.

  **Test cases**:
  - `test_returns_classify_response`: `run_inference(b"fake_image_bytes", model=fake_model)` →
    returns `ClassifyResponse` whose `.items` list contains dicts with `"label"`, `"confidence"`,
    `"hazardous"`.
  - `test_hazardous_tagged_correctly`: fake model returns class_id=6 (battery) with conf=0.90;
    assert `items[0]["hazardous"] is True`.
  - `test_non_hazardous_tagged_correctly`: fake model returns class_id=0 (paper) with conf=0.85;
    assert `items[0]["hazardous"] is False`.
  - `test_confidence_below_threshold_discarded`: fake model returns conf=0.30 (below default 0.50);
    assert `items` is empty.
  - `test_duplicate_label_keeps_highest_confidence`: fake model returns two detections for class 2
    (plastic) with conf 0.70 and 0.85; assert only one item in output with confidence 0.85.
  - `test_sorted_descending_by_confidence`: multiple detections with different confidences; assert
    output items are sorted highest-first.
  - `test_url_input_calls_fetch`: when `image_input` is a string URL, assert `safety.fetch_image_bytes`
    is called (patch it).
  - `test_api_dict_shape`: call `run_inference(...).to_api_dict()` and assert the top-level key
    is `"items"` only.

  File: `tests/test_vision_inference.py`

  Dependencies: `inference.py` (item 7), `schemas.py` (item 4), `safety.py` (item 5).

  Verify: `pytest tests/test_vision_inference.py -v` — all tests pass.

---

- [ ] 17. Write `tests/test_confidence.py` — tests for confidence threshold boundary behaviour.

  **Test cases**:
  - `test_high_threshold_boundary`: detections at 0.80 are labelled HIGH, at 0.799 MEDIUM.
  - `test_medium_threshold_boundary`: detections at 0.50 are labelled MEDIUM, at 0.499 DISCARD.
  - `test_discard_threshold_removes_item`: inference with a detection at 0.40 produces zero items.
  - `test_multiple_items_confidence_ordering`: give three fake detections with conf 0.60, 0.90, 0.75;
    assert output order is 0.90, 0.75, 0.60.
  - `test_confidence_rounded_to_2dp`: fake detection with raw conf 0.8567 → output confidence 0.86.
  - `test_all_above_threshold_returned`: five fake detections all above 0.50 → five items returned.

  File: `tests/test_confidence.py`

  Dependencies: `inference.py` (item 7), `schemas.py` (item 4).

  Verify: `pytest tests/test_confidence.py -v` — all tests pass.

---

- [ ] 18. Write `tests/test_class_mapping.py` — tests for dataset-mapping YAML correctness.

  **Test cases**:
  - `test_mapping_file_loads`: `dataset_mapping.yaml` loads without error and is a dict.
  - `test_all_non_null_values_are_canonical`: every non-`None` value in the mapping is one of
    the seven canonical class names from `classes.yaml`.
  - `test_known_source_classes_present`: assert the following keys exist: `plastic_bottle`,
    `glass_bottle`, `drink_can`, `paper`, `cardboard`, `battery`, `smartphone`, `laptop`,
    `electronic_device`.
  - `test_plastic_bottle_maps_to_plastic`: assert `mapping["plastic_bottle"] == "plastic"`.
  - `test_smartphone_maps_to_ewaste`: assert `mapping["smartphone"] == "ewaste"`.
  - `test_battery_maps_to_battery`: assert `mapping["battery"] == "battery"`.
  - `test_null_entries_are_discardable`: iterate all entries; any `None` value is acceptable
    (discard class); assert we have at least one non-null entry per canonical class.

  File: `tests/test_class_mapping.py`

  Dependencies: `dataset_mapping.yaml` (item 2), `classes.yaml` (item 2), `schemas.py` (item 4).

  Verify: `pytest tests/test_class_mapping.py -v` — all tests pass.

---

- [ ] 19. Create `ml/vision/requirements.txt` — pinned ML dependencies.

  List exact or tightly bounded versions to ensure reproducibility.  All packages must be
  CPU-installable without CUDA.

  Required entries:
  ```
  ultralytics==8.3.40
  torch==2.3.1
  torchvision==0.18.1
  PyYAML==6.0.2
  numpy==1.26.4
  Pillow==10.4.0
  ```

  Note in a comment that torch CPU-only wheels can be installed via:
  `pip install torch==2.3.1+cpu torchvision==0.18.1+cpu --index-url https://download.pytorch.org/whl/cpu`

  File: `ml/vision/requirements.txt`

  Verify: `python -c "import pkg_resources; pkg_resources.require(open('ml/vision/requirements.txt').read().splitlines())"` — no unmet requirement errors (only after `pip install`; CI step).

---

- [ ] 20. Write `ml/vision/README.md` — ML pipeline documentation.

  A standalone README focused on ML engineers setting up the pipeline.  Must cover:

  1. **Overview** — one paragraph explaining the YOLOv8n pipeline, its role in EcoLink, and
     how it relates to the existing Bedrock classifier (`backend/ecolink/core/classifier.py`).
  2. **Directory layout** — ASCII tree of `ml/vision/` with one-line descriptions per entry.
  3. **Installation** — `pip install -r ml/vision/requirements.txt`; CPU-only pip install
     command for torch.
  4. **Quick-start inference** — minimal Python snippet showing `from ml.vision.handler import
     classify; result = classify({"image_url": "https://bucket.s3.amazonaws.com/img.jpg"})`.
  5. **Dataset preparation** — how to drop raw data into `ml/vision/data/raw/`, run
     `prepare_dataset.py`, then `validate_dataset.py`.
  6. **Training** — `python ml/vision/train.py` with recommended CPU arguments.
  7. **Evaluation** — `python ml/vision/evaluate.py`.
  8. **Benchmarking** — `python ml/vision/scripts/benchmark.py`.
  9. **Class mapping** — table of the seven canonical classes with IDs and hazardous flag.
  10. **Configuration** — one-sentence description of each YAML file in `config/`.
  11. **Running tests** — `pytest tests/test_vision_*.py tests/test_confidence.py tests/test_class_mapping.py`.

  File: `ml/vision/README.md`

  Verify: file is valid Markdown, renders without error, contains all 11 sections listed above.

---

- [ ] 21. Append ML Vision section to root `README.md`.

  Do NOT rewrite or replace the existing README — append only.  Add a new top-level section
  `## ML Vision Pipeline` after the last existing section.

  The section must contain:
  - A one-paragraph summary of the vision pipeline.
  - A reference to `ml/vision/README.md` for full documentation.
  - The quick-start inference snippet (same as `ml/vision/README.md` item 4 above).
  - A note that the ML pipeline is CPU-first and does not require a GPU.

  File: `README.md` (append only)

  Verify: `python -c "c=open('README.md').read(); assert '## ML Vision Pipeline' in c; print('OK')"` — prints `OK`.

---

## Cross-File Dependency Map

```
classes.yaml ──────────────────────────────────► schemas.py
dataset_mapping.yaml ──────────────────────────► test_class_mapping.py
inference.yaml ────────────────────────────────► model_loader.py, inference.py
dataset.yaml ──────────────────────────────────► train.py, evaluate.py
schemas.py ────────────────────────────────────► inference.py, handler.py,
                                                  test_vision_schema.py,
                                                  test_vision_inference.py,
                                                  test_confidence.py,
                                                  test_class_mapping.py
safety.py ─────────────────────────────────────► inference.py,
                                                  test_vision_safety.py
model_loader.py ───────────────────────────────► inference.py
inference.py ──────────────────────────────────► handler.py,
                                                  test_vision_inference.py,
                                                  test_confidence.py
handler.py ─────────────────────────────────────► (callers: backend integration)
prepare_dataset.py ─────────────────────────────► validate_dataset.py
                                                  (run prepare before validate)
train.py ───────────────────────────────────────► evaluate.py, benchmark.py
                                                  (weights must exist first)
```

## Test Commands

```bash
# Install ML dependencies (CPU-only torch)
pip install torch==2.3.1+cpu torchvision==0.18.1+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r ml/vision/requirements.txt

# Run vision-specific tests (no weights required)
pytest tests/test_vision_schema.py tests/test_vision_safety.py \
       tests/test_vision_inference.py tests/test_confidence.py \
       tests/test_class_mapping.py -v

# Run the full existing test suite (must still pass)
pytest tests/ -v
```

## Assumptions & Notes

1. **ultralytics version**: `ultralytics==8.3.40` is used because it ships with YOLOv8n and
   has a stable `model.train()` / `model.predict()` / `model.val()` API.  If the environment
   has a different version pinned, the implementer should verify the `boxes.cls`, `boxes.conf`
   attribute names against the installed version's changelog.

2. **Existing tests must still pass**: the new `tests/test_vision_*.py` files are additive.
   The existing `conftest.py` inserts `backend/` into `sys.path`; the new test files must also
   insert `'.'` (the repo root) into `sys.path` so that `ml.vision.*` is importable.

3. **No torch/ultralytics at import time**: `model_loader.py` and `inference.py` import
   `ultralytics` inside functions (lazy import) so that the test files for `schemas.py` and
   `safety.py` can run in lightweight environments without torch installed.

4. **Root `README.md` — append only**: the instruction says do NOT rewrite it.  Use Python
   `open('README.md', 'a')` semantics: add a blank line then the new section at the end.

5. **`conftest.py` update**: the existing `tests/conftest.py` only adds `backend/` to sys.path.
   The new vision tests need `'.'` (repo root) on sys.path too.  Each new test file should
   add it locally at the top of the file with `sys.path.insert(0, str(pathlib.Path(__file__).
   resolve().parents[1]))` rather than modifying the shared conftest.
