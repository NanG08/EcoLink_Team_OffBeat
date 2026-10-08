"""
Thin HTTP client for the EcoLink backend API.
All methods raise EcoLinkAPIError on non-2xx responses.
Pass a mock instance of this class to tools during testing.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Optional
import requests

logger = logging.getLogger(__name__)


class EcoLinkAPIError(Exception):
    """Raised when the backend API returns an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


class EcoLinkAPIClient:
    """
    HTTP wrapper around the EcoLink backend API contract.

    Configuration via environment variables:
        EcoLink_API_BASE_URL  – e.g. https://api.EcoLink.example.com
        EcoLink_API_KEY       – Bearer token (injected by Lambda environment)
        EcoLink_REQUEST_TIMEOUT – seconds, default 10
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("EcoLink_API_BASE_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("EcoLink_API_KEY", "") or self._key_from_secret()
        self.timeout = timeout or int(os.environ.get("EcoLink_REQUEST_TIMEOUT", "10"))

        if not self.base_url:
            raise ValueError("EcoLink_API_BASE_URL must be set (env or constructor)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key_from_secret() -> str:
        """Fetch the API key from AWS Secrets Manager when EcoLink_API_KEY_SECRET_NAME is set,
        so it never has to sit in a plaintext Lambda environment variable."""
        secret_name = os.environ.get("EcoLink_API_KEY_SECRET_NAME")
        if not secret_name:
            return ""
        import boto3

        return boto3.client("secretsmanager").get_secret_value(SecretId=secret_name)["SecretString"].strip()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _raise_for_status(self, response: requests.Response) -> None:
        if not response.ok:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise EcoLinkAPIError(response.status_code, detail)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("GET %s params=%s", url, params)
        resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("POST %s body=%s", url, body)
        resp = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()

    def _patch(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("PATCH %s body=%s", url, body)
        resp = requests.patch(url, headers=self._headers(), json=body, timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Public API methods (mirror the contract exactly)
    # ------------------------------------------------------------------

    def classify_image(self, image_url: str) -> dict[str, Any]:
        """POST /classify → {items:[{label, confidence, hazardous}]}"""
        return self._post("/classify", {"image_url": image_url})

    def get_price(self, label: str, weight_kg: float) -> dict[str, Any]:
        """GET /price?label=&weight_kg= → {low, high, currency, updated}"""
        return self._get("/price", {"label": label, "weight_kg": weight_kg})

    def ewaste_quote(
        self, item_type: str, age_years: int, condition: str
    ) -> dict[str, Any]:
        """POST /ewaste/quote → {low, high, recommendation}"""
        return self._post(
            "/ewaste/quote",
            {"item_type": item_type, "age_years": age_years, "condition": condition},
        )

    def find_recyclers(
        self, lat: float, lng: float, labels: list[str]
    ) -> list[dict[str, Any]]:
        """GET /recyclers/match → [{recycler_id, name, distance_km, accepts}]"""
        return self._get(
            "/recyclers/match",
            {"lat": lat, "lng": lng, "labels": ",".join(labels)},
        )

    def create_pickup(
        self,
        user_id: str,
        items: list[dict[str, Any]],
        address_text: str,
        slot: str,
    ) -> dict[str, Any]:
        """POST /pickups → {pickup_id}"""
        return self._post(
            "/pickups",
            {
                "user_id": user_id,
                "items": items,
                "address_text": address_text,
                "slot": slot,
            },
        )

    def update_pickup(self, pickup_id: str, status: str) -> dict[str, Any]:
        """PATCH /pickups/{id} → {}"""
        return self._patch(f"/pickups/{pickup_id}", {"status": status})

    def offer_to_recycler(
        self, pickup_id: str, recycler_id: str
    ) -> dict[str, Any]:
        """POST /voice/offer → {}"""
        return self._post(
            "/voice/offer",
            {"pickup_id": pickup_id, "recycler_id": recycler_id},
        )
