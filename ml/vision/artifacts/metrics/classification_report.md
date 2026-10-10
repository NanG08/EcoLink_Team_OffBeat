# Per-class accuracy — test split (393 images)

Images: 393 · exact-match accuracy 0.69 · no prediction on 52 images

| Class | Accuracy | Precision | Recall | F1 | Support | |
|---|---:|---:|---:|---:|---:|---|
| paper | 0.929 | 0.775 | 0.620 | 0.689 | 50 | **weak** |
| cardboard | 0.970 | 0.929 | 0.812 | 0.867 | 48 |  |
| plastic | 0.924 | 0.833 | 0.435 | 0.571 | 46 | **weak** |
| metal | 0.888 | 0.604 | 0.582 | 0.593 | 55 | **weak** |
| glass | 0.942 | 0.786 | 0.930 | 0.852 | 71 |  |
| ewaste | 0.936 | 0.983 | 0.700 | 0.818 | 80 |  |
| battery | 0.972 | 0.969 | 0.873 | 0.918 | 71 |  |
| **macro avg** | 0.937 | 0.840 | 0.707 | 0.758 | | |

Hazard detection (photo contains e-waste or a battery → at least one `hazardous: true` item): precision 0.984, recall 0.788 (32 of 151 hazardous photos missed).

Weak classes (F1 < 0.7): paper, plastic, metal.

Accuracy counts true negatives, so it looks high for rare classes; read it with recall and F1.
