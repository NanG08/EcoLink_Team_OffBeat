"""Pickup lifecycle: validation, status transitions, voice offers and notifications.

Both PATCH /pickups/{id} and the recycler's keypad response (POST /voice/response) go
through `_move`, so SNS events and confirmation messages are identical either way.
Side effects (SNS, messages) are best-effort: a failure is logged and never undoes or
fails the status change.
"""

from __future__ import annotations

import logging
from typing import Any

from ecolink.core import messages
from ecolink.core.constants import (
    ALL_STATUSES,
    CONFIRMED,
    DECLINED,
    DIGIT_ACCEPT,
    DIGIT_DECLINE,
    HAZARDOUS_LABELS,
    LABELS,
    OFFERED,
    REQUESTED,
    TRANSITIONS,
)
from ecolink.core.http import ApiError, bad_request, conflict, not_found
from ecolink.core.log import log
from ecolink.core.matching import haversine_km
from ecolink.core.offers import AddressLeakError, assert_no_address, recycler_view
from ecolink.core.repository import ConflictError
from ecolink.core.validation import parse_number, parse_slot, require_str
from ecolink.providers.base import MessagingProvider, ProviderError, VoiceProvider

MAX_ITEMS = 20


def _clean_items(raw: Any) -> list[dict[str, Any]]:
    """Validate and whitelist item fields (label, weight_kg). Block hazardous labels."""
    if not isinstance(raw, list) or not raw:
        raise bad_request("'items' must be a non-empty list, e.g. [{\"label\": \"paper\", \"weight_kg\": 2}]")
    if len(raw) > MAX_ITEMS:
        raise bad_request(f"'items' may contain at most {MAX_ITEMS} entries")
    items: list[dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise bad_request(f"items[{i}] must be an object")
        label = str(entry.get("label", "")).strip().lower()
        if label not in LABELS:
            raise bad_request(f"items[{i}].label must be one of: {', '.join(LABELS)}")
        item: dict[str, Any] = {"label": label}
        if entry.get("weight_kg") is not None:
            item["weight_kg"] = parse_number(
                entry["weight_kg"], f"items[{i}].weight_kg", lo=0, hi=1000, lo_exclusive=True
            )
        items.append(item)
    hazardous = sorted({it["label"] for it in items if it["label"] in HAZARDOUS_LABELS})
    if hazardous:
        raise ApiError(
            422,
            "hazardous_items_blocked",
            f"{', '.join(hazardous)} cannot be collected by a regular pickup; "
            "send the user to an authorised drop-off point instead",
        )
    return items


class PickupService:
    def __init__(
        self,
        repo: Any,
        notifier: Any,
        messaging: MessagingProvider,
        voice: VoiceProvider | None = None,
    ) -> None:
        self.repo = repo
        self.notifier = notifier
        self.messaging = messaging
        self.voice = voice

    # ── create ─────────────────────────────────────────────────────────────────
    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        user_id = require_str(data, "user_id", 128)
        items = _clean_items(data.get("items"))
        address_text = require_str(data, "address_text", 500)
        slot = parse_slot(data.get("slot"))
        pickup = self.repo.create_pickup(user_id, items, address_text, slot)
        log("pickup_created", pickup_id=pickup["pickup_id"], user_id=user_id)
        self._publish(pickup, None)
        return pickup

    # ── status changes ─────────────────────────────────────────────────────────
    def transition(self, pickup_id: str, new_status: str) -> dict[str, Any]:
        """PATCH /pickups/{id}. Same-status requests are idempotent no-ops."""
        if new_status not in ALL_STATUSES:
            raise bad_request(f"'status' must be one of: {', '.join(ALL_STATUSES)}")
        pickup = self._get(pickup_id)
        current = pickup["status"]
        if new_status == current:
            return pickup
        if new_status == OFFERED:
            raise conflict(
                "use_voice_offer",
                "A pickup becomes 'offered' only via POST /voice/offer (which calls the recycler)",
            )
        self._check_transition(current, new_status)
        if new_status == CONFIRMED and not pickup.get("recycler_id"):
            raise conflict("no_recycler_assigned", "Cannot confirm a pickup that has no recycler")
        return self._move(pickup, new_status)

    def record_recycler_response(self, pickup_id: str, digit: str) -> dict[str, Any]:
        """The recycler pressed `digit` on the call: 1 accept, 2 decline."""
        if digit == DIGIT_ACCEPT:
            target = CONFIRMED
        elif digit == DIGIT_DECLINE:
            target = DECLINED
        else:
            raise bad_request("'digit' must be 1 (accept) or 2 (decline)", "invalid_digit")
        pickup = self._get(pickup_id)
        if pickup["status"] == target:  # provider retry / double press
            return pickup
        self._check_transition(pickup["status"], target)
        return self._move(pickup, target)

    # ── voice offer ────────────────────────────────────────────────────────────
    def offer(self, pickup_id: str, recycler_id: str, callback_url: str) -> tuple[dict[str, Any], str]:
        """Phone the recycler. The offer payload never contains the household address."""
        if self.voice is None:
            raise RuntimeError("PickupService was built without a voice provider")
        pickup = self._get(pickup_id)
        if pickup["status"] not in (REQUESTED, DECLINED):
            raise conflict(
                "invalid_status_transition",
                f"Only 'requested' or 'declined' pickups can be offered (current: {pickup['status']})",
            )
        recycler = self.repo.get_recycler(recycler_id)
        if not recycler:
            raise not_found(f"Recycler '{recycler_id}' not found")
        if recycler.get("available") is not True:
            raise conflict("recycler_unavailable", f"Recycler '{recycler_id}' is not available")
        needed = {it["label"] for it in pickup["items"]}
        if not needed <= set(recycler.get("accepts") or []):
            raise conflict("recycler_cannot_accept", "Recycler does not accept all items in this pickup")
        phone = recycler.get("phone")
        if not phone:
            raise conflict("recycler_unreachable", "Recycler has no phone number on file")

        offered = self._move(pickup, OFFERED, set_fields={"recycler_id": recycler_id})
        payload = recycler_view(offered, recycler, self._distance_km(offered, recycler))
        try:
            assert_no_address(payload, offered)
            call_id = self.voice.place_offer_call(phone, payload, callback_url)
        except (ProviderError, AddressLeakError) as exc:
            log("offer_failed", logging.ERROR, pickup_id=pickup_id, recycler_id=recycler_id, error=str(exc))
            try:  # roll back to the queue so the pickup can be offered again
                self._move(offered, REQUESTED, remove_fields=("recycler_id",))
            except ApiError:
                log("offer_rollback_failed", logging.ERROR, pickup_id=pickup_id)
            if isinstance(exc, AddressLeakError):
                raise ApiError(500, "internal_error", "Offer was blocked by the address-sharing rule") from exc
            raise ApiError(502, "voice_provider_error", "Could not place the call to the recycler") from exc
        log("offer_sent", pickup_id=pickup_id, recycler_id=recycler_id, call_id=call_id)
        return offered, call_id

    # ── internals ──────────────────────────────────────────────────────────────
    def _get(self, pickup_id: str) -> dict[str, Any]:
        pickup = self.repo.get_pickup(pickup_id)
        if not pickup:
            raise not_found(f"Pickup '{pickup_id}' not found")
        return pickup

    @staticmethod
    def _check_transition(current: str, new: str) -> None:
        if new not in TRANSITIONS.get(current, frozenset()):
            raise conflict(
                "invalid_status_transition",
                f"Cannot change a pickup from '{current}' to '{new}'",
            )

    def _move(
        self,
        pickup: dict[str, Any],
        new_status: str,
        *,
        set_fields: dict[str, Any] | None = None,
        remove_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        previous = pickup["status"]
        try:
            updated = self.repo.transition_pickup(
                pickup["pickup_id"], previous, new_status,
                set_fields=set_fields, remove_fields=remove_fields,
            )
        except ConflictError:
            raise conflict(
                "status_conflict", "The pickup was changed by someone else; please retry"
            ) from None
        log("pickup_status_changed", pickup_id=updated["pickup_id"], previous=previous, status=new_status)
        self._publish(updated, previous)
        if new_status == CONFIRMED:
            self._notify_confirmed(updated)
        elif new_status == DECLINED:
            self._notify_declined(updated)
        return updated

    def _distance_km(self, pickup: dict[str, Any], recycler: dict[str, Any]) -> float | None:
        user = self.repo.get_user(pickup["user_id"])
        try:
            return haversine_km(
                float(user["lat"]), float(user["lng"]), float(recycler["lat"]), float(recycler["lng"])
            )
        except (TypeError, KeyError, ValueError):
            return None

    def _publish(self, pickup: dict[str, Any], previous: str | None) -> None:
        try:
            self.notifier.pickup_status_changed(pickup, previous)
        except Exception:  # noqa: BLE001
            logging.getLogger("ecolink").exception("sns_publish_failed pickup_id=%s", pickup["pickup_id"])

    def _send(self, identifier: str, text: str) -> None:
        try:
            self.messaging.send_text(self.messaging.address_for(identifier), text)
        except Exception:  # noqa: BLE001
            logging.getLogger("ecolink").exception("message_send_failed to=%s", identifier)

    def _notify_confirmed(self, pickup: dict[str, Any]) -> None:
        recycler = self.repo.get_recycler(pickup.get("recycler_id", "")) or {}
        user = self.repo.get_user(pickup["user_id"]) or {}
        # Status is `confirmed`, so this view (and only now) includes the address.
        view = recycler_view(pickup, recycler or None)
        if recycler.get("phone"):
            self._send(recycler["phone"], messages.recycler_pickup_confirmed(recycler.get("lang"), view))
        self._send(
            pickup["user_id"],
            messages.user_pickup_confirmed(user.get("lang"), recycler.get("name", "Your kabadiwala"), pickup["slot"]),
        )

    def _notify_declined(self, pickup: dict[str, Any]) -> None:
        recycler = self.repo.get_recycler(pickup.get("recycler_id", "")) or {}
        user = self.repo.get_user(pickup["user_id"]) or {}
        self._send(
            pickup["user_id"],
            messages.user_pickup_declined(user.get("lang"), recycler.get("name", "The kabadiwala")),
        )
