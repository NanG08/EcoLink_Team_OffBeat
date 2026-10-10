"""Shared constants: item labels, pickup statuses and the allowed status transitions."""

from __future__ import annotations

# Labels from the API contract (POST /classify).
LABELS = ("paper", "cardboard", "plastic", "metal", "glass", "ewaste", "battery")

# Hazardous labels ALWAYS go to safe drop-off, never a normal pickup.
HAZARDOUS_LABELS = frozenset({"ewaste", "battery"})

# Pickup statuses from the API contract (PATCH /pickups/{id}).
REQUESTED = "requested"
OFFERED = "offered"
CONFIRMED = "confirmed"
COMPLETED = "completed"
CANCELLED = "cancelled"
DECLINED = "declined"

ALL_STATUSES = (REQUESTED, OFFERED, CONFIRMED, COMPLETED, CANCELLED, DECLINED)

# status -> statuses it may move to.
TRANSITIONS: dict[str, frozenset[str]] = {
    REQUESTED: frozenset({OFFERED, CANCELLED}),
    # offered -> requested is the rollback used when the voice call cannot be placed.
    OFFERED: frozenset({CONFIRMED, DECLINED, CANCELLED, REQUESTED}),
    # A declined pickup can be re-offered to another recycler or put back in the queue.
    DECLINED: frozenset({OFFERED, REQUESTED, CANCELLED}),
    CONFIRMED: frozenset({COMPLETED, CANCELLED}),
    COMPLETED: frozenset(),
    CANCELLED: frozenset(),
}

# ADDRESS RULE: the household address (and contact identity) may only be shown to a
# recycler in these statuses. Every recycler-facing payload goes through
# core.offers, which consults this set.
ADDRESS_VISIBLE_STATUSES = frozenset({CONFIRMED, COMPLETED})

# Voice keypad digits pressed by the recycler.
DIGIT_ACCEPT = "1"
DIGIT_DECLINE = "2"
