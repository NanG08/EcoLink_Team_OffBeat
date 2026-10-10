# Data Collection Checklist — EcoLink waste photos

Goal: photos that look like what households actually send on WhatsApp, labelled so the
YOLOv8n detector can learn all 7 classes: `paper`, `cardboard`, `plastic`, `metal`, `glass`,
`ewaste`, `battery`.

Why we need it: the current model is trained on public data. Its weakest classes are **plastic,
metal and paper** (F1 0.57–0.69), and its battery photos are mostly studio product shots (see
`artifacts/metrics/classification_report.md`). Real local photos fix both.

## 1. How many photos per class

| Class | Minimum | Target | Must include |
|---|---:|---:|---|
| paper | 150 | 400 | newspaper stacks, notebooks, loose sheets, paper bags, cups |
| cardboard | 150 | 400 | flattened boxes, delivery cartons, egg trays, tetra-paks |
| plastic | 200 | 500 | PET bottles, milk pouches, polythene, containers, buckets, wrappers |
| metal | 200 | 500 | tins/cans, utensils, iron scrap, aluminium foil, wires |
| glass | 150 | 400 | bottles (clear/green/brown), jars, broken glass in a box |
| ewaste | 200 | 500 | phones, chargers, cables, remotes, keyboards, laptops, small appliances |
| battery | 200 | 500 | AA/AAA cells, button cells, phone batteries, inverter/UPS batteries, power banks |

* Count **photos**, not objects. Aim for ≥ 1.5 boxed objects per photo on average.
* Plastic, metal and battery get higher targets: plastic and metal are the weakest classes, and
  battery has the biggest gap between the current training data and real WhatsApp photos.
* Roughly 30% of photos should be **mixed scenes** (2–4 classes together, e.g. a kabadi bag).
* Add ~10% **negatives**: photos with no recyclables (food, clothes, an empty floor) and no boxes.
  They reduce false positives.

## 2. What to vary (tick each one for every class)

**Backgrounds**
- [ ] kitchen counter / floor tiles
- [ ] balcony or doorstep, the typical kabadi handover spot
- [ ] inside a sack, bucket or dustbin
- [ ] bed sheet / plain cloth
- [ ] outdoor ground (soil, concrete)
- [ ] cluttered shelf or drawer (especially for batteries and chargers)

**Lighting**
- [ ] daylight near a window
- [ ] indoor tube light / LED at night
- [ ] dim room (no flash)
- [ ] with phone flash
- [ ] harsh sun with strong shadows

**Camera**
- [ ] at least 3 different phones, including one budget phone
- [ ] distances: close-up (item fills the frame), arm's length, whole pile from standing height
- [ ] angles: top-down, 45°, side-on
- [ ] some photos sent **through WhatsApp and saved back** (recompressed), the real input format
- [ ] some slightly blurry or partly cut-off items, as real users send them

**Object state**
- [ ] clean and dirty / crushed / torn / wet
- [ ] labels facing the camera and facing away
- [ ] single item and stacks or bundles

## 3. Privacy and consent (do not skip)

- [ ] No faces, no people, no house numbers, addresses, documents or screens showing personal data
- [ ] Phone screens off, or showing nothing personal
- [ ] Strip location metadata (EXIF GPS) before sharing photos
- [ ] Written consent from every household that contributes photos
- [ ] Keep raw photos in private storage. Never commit them to git (`ml/vision/.gitignore`
      already ignores `data/`).

## 4. File naming = fair splits

Name every photo `<session>_<free text>.jpg`, e.g. `s07_balcony_0012.jpg`.
A **session** is one person, one place, one phone, one sitting.

Photos from the same session share background, lighting and often the same items. If one
session's photos land in both train and test, the test score is inflated, so **whole sessions
go to exactly one split**:

1. Aim for at least 20 sessions in total, and at least 3 sessions per class.
2. Assign sessions, not photos: about 70% of sessions to train, 15% to val and 15% to test.
   Every class must appear in val and test.
3. Keep the **test** sessions frozen once chosen; never tune thresholds on them.
4. Put different phones in each split, so the test set measures generalisation to new phones.
5. Before training, look for near-duplicate photos across splits (burst shots) and move or delete
   them.

## 5. Labelling (boxes)

The model is a detector, so every object needs a **bounding box** with a class name.

* Use a free tool that exports **Pascal VOC XML**: LabelImg, CVAT, Label Studio or
  makesense.ai. Use exactly the 7 class names above (or a name listed in
  `config/dataset_mapping.yaml`, e.g. `plastic_bottle` or `batteries`).
* Box every visible item of a class, even partly hidden ones. Never leave an item unboxed;
  an unboxed item teaches the model that it is background.
* Battery inside a device: box the device as `ewaste`.
* Plain-background single-item shots can be auto-boxed with
  `scripts/autolabel_batteries.py --images <folder> --label <class>`.
  Its background-subtraction step is class-agnostic; spot-check the `--sheet` output.

## 6. From photos to training data (existing tools only)

```
data/collected/
  train/   <- all photos + .xml of the TRAIN sessions
  val/     <- all photos + .xml of the VAL sessions
  test/    <- all photos + .xml of the TEST sessions
```
```bash
python ml/vision/scripts/prepare_dataset.py --source-dir data/collected/train --format pascal-voc --split 1 0 0
python ml/vision/scripts/prepare_dataset.py --source-dir data/collected/val   --format pascal-voc --split 0 1 0
python ml/vision/scripts/prepare_dataset.py --source-dir data/collected/test  --format pascal-voc --split 0 0 1
python ml/vision/scripts/validate_dataset.py      # also flags the same filename in two splits
python ml/vision/train.py --imgsz 416 --epochs 30
python ml/vision/scripts/classification_report.py # per-class accuracy, weak classes flagged
```
Running `prepare_dataset.py` once per split folder keeps every session in the split you chose
(its own random split would scatter a session across splits). The photos are added next to the
public data already in `ml/vision/data/`.

## 7. Done when

- [ ] Every class meets its minimum, and every class has ≥ 30 photos in val and in test
- [ ] Every section-2 checkbox is covered for every class
- [ ] `validate_dataset.py` passes
- [ ] No session appears in more than one split
- [ ] The privacy checklist is complete
