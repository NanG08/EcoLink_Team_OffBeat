"""
Tests for dataset_mapping.yaml and classes.yaml correctness.
Pure Python — no GPU, no model weights.
"""
import os
import sys
from pathlib import Path

# Ensure repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import yaml

from ml.vision.schemas import load_classes_config

_CONFIG_DIR = Path(__file__).parent.parent / "ml" / "vision" / "config"


@pytest.fixture(scope="module")
def mapping() -> dict:
    with open(_CONFIG_DIR / "dataset_mapping.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("mappings", {})


@pytest.fixture(scope="module")
def discard_list() -> list:
    with open(_CONFIG_DIR / "dataset_mapping.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("discard", [])


@pytest.fixture(scope="module")
def classes_data() -> dict:
    with open(_CONFIG_DIR / "classes.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─────────────────────────────────────────────────────────────────────────────
# dataset_mapping.yaml tests
# ─────────────────────────────────────────────────────────────────────────────

def test_mapping_file_loads(mapping: dict) -> None:
    """dataset_mapping.yaml loads without error and is a dict."""
    assert isinstance(mapping, dict)
    assert len(mapping) > 0


def test_mappings_plastic_bottle(mapping: dict) -> None:
    assert mapping.get("plastic_bottle") == "plastic"


def test_mappings_battery(mapping: dict) -> None:
    assert mapping.get("battery") == "battery"


def test_mappings_batteries_to_battery(mapping: dict) -> None:
    assert mapping.get("batteries") == "battery"


def test_mappings_smartphone(mapping: dict) -> None:
    assert mapping.get("smartphone") == "ewaste"


def test_mappings_laptop(mapping: dict) -> None:
    assert mapping.get("laptop") == "ewaste"


def test_mappings_electronic_device(mapping: dict) -> None:
    assert mapping.get("electronic_device") == "ewaste"


def test_mappings_glass_bottle(mapping: dict) -> None:
    assert mapping.get("glass_bottle") == "glass"


def test_mappings_drink_can(mapping: dict) -> None:
    assert mapping.get("drink_can") == "metal"


def test_mappings_newspaper(mapping: dict) -> None:
    assert mapping.get("newspaper") == "paper"


def test_mappings_cardboard_box(mapping: dict) -> None:
    assert mapping.get("cardboard_box") == "cardboard"


def test_unknown_class_not_in_mappings(mapping: dict) -> None:
    """An unmapped class is simply absent from mappings (and would be discarded)."""
    assert "unknown_class" not in mapping
    assert "some_random_thing" not in mapping


def test_all_non_null_values_are_canonical(mapping: dict) -> None:
    """Every non-null mapping value must be one of the 7 canonical class names."""
    cfg = load_classes_config()
    canonical = set(cfg.class_names)
    for src, tgt in mapping.items():
        if tgt is not None:
            assert tgt in canonical, (
                f"Mapping '{src}' → '{tgt}' is not a canonical class"
            )


def test_all_7_canonical_classes_appear_in_values(mapping: dict) -> None:
    """All 7 canonical classes must appear as a target in at least one mapping."""
    cfg = load_classes_config()
    mapped_targets = {v for v in mapping.values() if v is not None}
    for cls in cfg.class_names:
        assert cls in mapped_targets, f"No mapping found for canonical class '{cls}'"


def test_discard_list_not_empty(discard_list: list) -> None:
    assert len(discard_list) > 0


def test_food_in_discard_list(discard_list: list) -> None:
    assert "food" in discard_list


def test_organic_in_discard_list(discard_list: list) -> None:
    assert "organic" in discard_list


# ─────────────────────────────────────────────────────────────────────────────
# classes.yaml tests
# ─────────────────────────────────────────────────────────────────────────────

def test_classes_yaml_class_names_length(classes_data: dict) -> None:
    assert len(classes_data["class_names"]) == 7


def test_classes_yaml_class_names_order(classes_data: dict) -> None:
    expected = ["paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery"]
    assert classes_data["class_names"] == expected


def test_class_ids_paper(classes_data: dict) -> None:
    assert classes_data["class_ids"]["paper"] == 0


def test_class_ids_battery(classes_data: dict) -> None:
    assert classes_data["class_ids"]["battery"] == 6


def test_class_ids_ewaste(classes_data: dict) -> None:
    assert classes_data["class_ids"]["ewaste"] == 5


def test_hazardous_classes_contains_ewaste(classes_data: dict) -> None:
    key = "hazardous_classes" if "hazardous_classes" in classes_data else "hazardous_labels"
    assert "ewaste" in classes_data[key]


def test_hazardous_classes_contains_battery(classes_data: dict) -> None:
    key = "hazardous_classes" if "hazardous_classes" in classes_data else "hazardous_labels"
    assert "battery" in classes_data[key]


def test_hazardous_classes_does_not_contain_paper(classes_data: dict) -> None:
    key = "hazardous_classes" if "hazardous_classes" in classes_data else "hazardous_labels"
    assert "paper" not in classes_data[key]


# ─────────────────────────────────────────────────────────────────────────────
# load_classes_config round-trip
# ─────────────────────────────────────────────────────────────────────────────

def test_load_classes_config_roundtrip() -> None:
    cfg = load_classes_config()
    assert len(cfg.class_names) == 7
    assert cfg.id_for("paper") == 0
    assert cfg.id_for("ewaste") == 5
    assert cfg.id_for("battery") == 6
    assert cfg.is_hazardous("ewaste") is True
    assert cfg.is_hazardous("battery") is True
    assert cfg.is_hazardous("plastic") is False
