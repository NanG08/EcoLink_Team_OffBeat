# EcoLink ML — classifier, serving, pricing & e-waste quote

Covers the five deliverables of the ML role (vision classifier, serving, pricing,
e-waste quote and the data-collection checklist). Everything runs on CPU; dependencies are
pinned; accuracy is reported per class, including the weak ones.

| # | Deliverable | Where |
|---|-------------|-------|
| 1 | Classifier: YOLOv8n fine-tuned for the 7 labels; augmentation, training, evaluation, per-class accuracy table, confusion matrix, export | [`vision/`](vision/README.md): `train.py`, `evaluate.py`, `scripts/classification_report.py`, `export.py` |
| 2 | Serving: Lambda container for `POST /classify` with `hazardous` and a low-confidence flag | [`serving/`](serving/): `lambda_handler.py`, `Dockerfile`, `template.yaml` |
| 3 | Pricing: `rate_table.json` (low/high ₹ per kg, `updated`, `source`, placeholders marked) + `GET /price` | [`pricing/`](pricing/): `rate_table.json`, `rates.py`, `seed_rates.py` |
| 4 | E-waste quote: rule-based `{low, high, recommendation}` + assumptions table, no partnership claims | [`pricing/rates.py`](pricing/rates.py), [`pricing/ASSUMPTIONS.md`](pricing/ASSUMPTIONS.md) |
| 5 | Data collection checklist | [`vision/DATA_COLLECTION.md`](vision/DATA_COLLECTION.md) |

**Model choice.** The classifier is a YOLOv8n **detector** rather than a MobileNetV3/EfficientNet
image classifier. A WhatsApp photo usually holds several items, and the API returns one entry per
item type. Training data is therefore in YOLO box format, not `data/train/<label>/` folders. The
checklist explains how to collect and label photos for it.

## Setup (once)

```bash
python -m venv .venv && .venv\Scripts\activate          # Python 3.11/3.12; Linux/macOS: source .venv/bin/activate
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r ml/vision/requirements.txt -r ml/requirements-tools.txt
```
Set `YOLO_AUTOINSTALL=false` so ultralytics never pip-installs packages on its own.

## 1. Train · evaluate · export

```bash
# data (already prepared under ml/vision/data; see vision/README.md §8 to rebuild it)
python ml/vision/scripts/validate_dataset.py

# train (12 epochs ≈ 2.8 h on an MX330 GPU; ≈ 50 min/epoch on a laptop CPU)
python ml/vision/train.py --epochs 12 --batch-size 16 --imgsz 416 --workers 2 --patience 5

# box-level metrics (precision / recall / mAP) + confusion matrix image
python ml/vision/evaluate.py --imgsz 416 --conf 0.001

# per-class ACCURACY table through the real API pipeline (weak classes flagged)
python ml/vision/scripts/classification_report.py --split test

# export to ONNX and verify against the PyTorch model
python ml/vision/export.py
```
Outputs land in `ml/vision/artifacts/`: `weights/best.pt`, `weights/best.onnx`,
`confusion_matrix.png`, `metrics/classification_report.{json,md}`,
`metrics/classification_confusion.png` and `metrics/export_report.json`.

### Results on the held-out test split (393 photos)

Per photo, through the deployed pipeline (threshold 0.50):

| Class | Accuracy | Precision | Recall | F1 | |
|---|---:|---:|---:|---:|---|
| paper | 0.929 | 0.775 | 0.620 | 0.689 | **weak** |
| cardboard | 0.970 | 0.929 | 0.812 | 0.867 | |
| plastic | 0.924 | 0.833 | 0.435 | 0.571 | **weak** |
| metal | 0.888 | 0.604 | 0.582 | 0.593 | **weak** |
| glass | 0.942 | 0.786 | 0.930 | 0.852 | |
| ewaste | 0.936 | 0.983 | 0.700 | 0.818 | |
| battery | 0.972 | 0.969 | 0.873 | 0.918 | |
| macro avg | 0.937 | 0.840 | 0.707 | 0.758 | |

* Detection mAP50 / mAP50-95: **0.59 / 0.43**.
* Hazard detection: precision 0.98, **recall 0.79**. 32 of 151 photos with e-waste or batteries
  were not flagged, so it is the main safety gap.
* Weak classes: **plastic, metal, paper**. Most errors are missed objects (no prediction on 52
  photos), not confusions between classes.
* Battery scores are optimistic: training and test battery photos are mostly studio product shots
  with machine-generated boxes.
* "Accuracy" counts true negatives, so it looks high for every class. Read it together with recall
  and F1.
* ONNX export: numerically identical to PyTorch on the same input (max Δ 0.000002) and 2.8× faster
  on CPU (93 vs 263 ms). Through the pipeline, its square padding changes 4 of 50 near-threshold
  answers, so re-check the numbers above if you deploy the ONNX model.

## 2. Serve `POST /classify`

Response (contract fields unchanged, one field added per item):
```json
{"items": [
  {"label": "ewaste",  "confidence": 0.87, "hazardous": true,  "requires_confirmation": false},
  {"label": "glass",   "confidence": 0.65, "hazardous": false, "requires_confirmation": true}
]}
```
* `hazardous` is always `true` for `ewaste` and `battery`.
* `requires_confirmation` uses the ML pipeline's own rule (`ml/vision/safety.py`, thresholds in
  `vision/config/inference.yaml`). It is true when a regular item is below **0.80** or a hazardous
  item below **0.70**, and the agent should then ask the user to confirm the item.
  ⚠ `conversation/flow.json` uses a single 0.75 cut-off (`confidence_accept`) instead. Both
  were left unchanged; the team should pick one.
* The same container also serves `GET /price`, `POST /ewaste/quote` and `GET /health` from
  `rate_table.json`, so the whole flow demos without DynamoDB.
* Errors: `{"error": <code>, "detail": <message>}`, the backend's format (400 bad input/URL,
  422 hazardous price, 502 image download failed, 404 unknown route).

**Try it locally (no Docker):**
```bash
python -c "from ml.serving.lambda_handler import handler; import json; print(handler({'httpMethod':'POST','resource':'/classify','body':json.dumps({'image_url':'https://open-images-dataset.s3.amazonaws.com/test/06a1131d642da3e4.jpg'})}, None)['body'])"
```
Image URLs must be `https://*.amazonaws.com` (pre-signed S3); anything else is rejected.

**Container (from the repo root):**
```bash
docker build -f ml/serving/Dockerfile -t ecolink-classifier .
docker run --rm -p 9000:8080 ecolink-classifier
curl -s localhost:9000/2015-03-31/functions/function/invocations -d '{"httpMethod":"GET","resource":"/health"}'
```

### Deploy

*Option A, AWS SAM (simplest):*
```bash
sam build -t ml/serving/template.yaml
sam deploy --guided            # stack name e.g. ecolink-classifier, region ap-south-1
```
This creates the container Lambda (3008 MB, 30 s) with an IAM-auth function URL. Outputs:
`ClassifierFunctionArn`, `ClassifierFunctionUrl`.

*Option B, plain AWS CLI:*
```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text); REGION=ap-south-1
aws ecr create-repository --repository-name ecolink-classifier --region $REGION
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker tag ecolink-classifier $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/ecolink-classifier:v1
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/ecolink-classifier:v1
aws lambda create-function --function-name ecolink-classifier --package-type Image \
  --code ImageUri=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/ecolink-classifier:v1 \
  --role arn:aws:iam::$ACCOUNT:role/<lambda-basic-execution-role> --memory-size 3008 --timeout 30 --region $REGION
```

*Route the existing API's `/classify` to it* (keeps the API-key authorizer; today the route goes
to the Bedrock proxy):
```bash
API_ID=<EcoLink-api id>; FN_ARN=<ClassifierFunctionArn>
RES_ID=$(aws apigateway get-resources --rest-api-id $API_ID --query "items[?path=='/classify'].id" --output text)
aws apigateway put-integration --rest-api-id $API_ID --resource-id $RES_ID --http-method POST \
  --type AWS_PROXY --integration-http-method POST \
  --uri arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$FN_ARN/invocations
aws lambda add-permission --function-name $FN_ARN --statement-id apigw-classify \
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*/POST/classify"
aws apigateway create-deployment --rest-api-id $API_ID --stage-name prod
```
The next `cdk deploy` resets this route. Making the switch permanent means changing
`infra/ecolink_stack.py`, which was intentionally not modified.

## 3–4. Pricing and e-waste quote

* `pricing/rate_table.json` holds every rate, the `updated` date and a `source` note.
  **All values are placeholders** (`"is_placeholder": true`) until rates are collected locally
  ([how](pricing/ASSUMPTIONS.md#3-replacing-placeholders)).
* `pricing/rates.py` provides `price_estimate(label, weight_kg)`, `ewaste_quote(item_type, age_years, condition)`
  and `explain_quote(...)`, which returns every factor behind a quote. The arithmetic is the
  deployed backend's own (`backend/ecolink/core/pricing.py`), so the numbers always match the
  live API.
* The deployed `GET /price` and `POST /ewaste/quote` read DynamoDB. To load the table into it:
  ```bash
  python ml/pricing/seed_rates.py --dry-run     # validate
  python ml/pricing/seed_rates.py               # upsert; run AFTER scripts/seed.py
  ```
  `GET /price` then reports the rate data's real `updated` date. `scripts/seed.py` alone stamps
  the seeding date, which makes stale demo rates look current.
* Assumptions, formulas, worked examples and known limitations are in
  [`pricing/ASSUMPTIONS.md`](pricing/ASSUMPTIONS.md). Batteries always get `dropoff`.
  EcoLink claims no store partnership.

```python
from ml.pricing import rates
rates.price_estimate("paper", 3)            # {'low': 30, 'high': 42, 'currency': 'INR', 'updated': '2026-10-10'}
rates.ewaste_quote("laptop", 2, "working")  # {'low': 10500, 'high': 24500, 'recommendation': 'sell'}
```

## 5. Collecting real photos

See [`vision/DATA_COLLECTION.md`](vision/DATA_COLLECTION.md). It covers photos per class (plastic,
metal and battery get the largest targets), backgrounds, lighting, phones, privacy and labelling,
plus a fair split: whole capture sessions go to train, val or test, never individual photos.

## Tests

```bash
python -m pytest tests/test_pricing_rate_table.py tests/test_serving_handler.py tests/test_classification_report.py
python -m pytest -m integration          # needs best.pt + data (real model, ONNX export)
```
Plus the vision suites listed in [`vision/README.md`](vision/README.md) §16.

## Not done / known gaps

* The container image was not built in this environment (Docker daemon not running). Its
  dependency lock resolves for Linux/Python 3.12, the template passes `cfn-lint`, and the handler is
  tested in-process, including against the real model and a real S3 URL.
* `/classify` in the deployed API still routes to the Bedrock classifier until it is re-pointed
  (above).
* Rates are placeholders.
* The confirmation threshold differs from `conversation/flow.json` (0.80/0.70 vs 0.75).
