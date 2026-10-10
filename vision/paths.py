"""
Repo-relative path resolution for the EcoLink vision pipeline.

Config files store paths relative to the repo root (e.g.
``ml/vision/artifacts/weights/best.pt``).  Resolving them against the current
working directory breaks notebooks, Lambda and any script not started from the
repo root, so every module resolves them here instead.

Zero heavy dependencies: stdlib + PyYAML only.
"""
from __future__ import annotations

from pathlib import Path

import yaml

VISION_DIR = Path(__file__).resolve().parent
REPO_ROOT = VISION_DIR.parent.parent
ARTIFACTS_DIR = VISION_DIR / "artifacts"


def repo_path(path: str | Path) -> Path:
    """Return *path* unchanged if absolute, else resolved against the repo root."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def resolve_dataset_yaml(data: str | Path, out_dir: Path | None = None) -> Path:
    """
    Return a dataset YAML whose ``path:`` key is absolute.

    Ultralytics resolves a relative ``path:`` against its global
    ``datasets_dir`` setting — not the YAML's location or the CWD — so the
    repo's ``path: ml/vision/data`` points at a non-existent directory.  When
    the key is relative, a resolved copy is written to *out_dir*
    (default: ``ml/vision/artifacts``) and that copy's path is returned.
    """
    data_path = repo_path(data)
    cfg = yaml.safe_load(data_path.read_text(encoding="utf-8"))
    root = Path(cfg.get("path") or ".")
    if root.is_absolute():
        return data_path
    cfg["path"] = str(repo_path(root))
    out_dir = out_dir or ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved = out_dir / f"{data_path.stem}.resolved.yaml"
    resolved.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return resolved
