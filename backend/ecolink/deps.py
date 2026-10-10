"""Lazily-built singletons shared by handlers (re-used across warm Lambda invocations).

Tests replace them with `override(...)`; call `reset()` to clear.
"""

from __future__ import annotations

from typing import Any, Callable

from ecolink.core.notifications import SnsNotifier
from ecolink.core.pickup_service import PickupService
from ecolink.core.repository import Repository
from ecolink.core.storage import ImageStore
from ecolink.providers import build_messaging_provider, build_voice_provider

_instances: dict[str, Any] = {}


def _get(name: str, factory: Callable[[], Any]) -> Any:
    if name not in _instances:
        _instances[name] = factory()
    return _instances[name]


def get_repo() -> Any:
    return _get("repo", Repository)


def get_notifier() -> Any:
    return _get("notifier", SnsNotifier)


def get_messaging() -> Any:
    return _get("messaging", build_messaging_provider)


def get_voice() -> Any:
    return _get("voice", build_voice_provider)


def get_image_store() -> Any:
    return _get("image_store", ImageStore)


def get_service() -> PickupService:
    return PickupService(get_repo(), get_notifier(), get_messaging(), get_voice())


def override(**instances: Any) -> None:
    """override(repo=..., notifier=..., messaging=..., voice=..., image_store=...)"""
    _instances.update(instances)


def reset() -> None:
    _instances.clear()
