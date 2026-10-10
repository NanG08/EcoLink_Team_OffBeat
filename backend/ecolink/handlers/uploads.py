"""POST /uploads/presign {user_id, content_type} -> pre-signed S3 POST for a private image.

The client POSTs the file to `upload_url` with `fields` + the file as multipart form
data, then passes `image_url` (a time-limited pre-signed GET) to /classify.
Images auto-expire after 7 days (bucket lifecycle rule).
"""

from __future__ import annotations

from typing import Any

from ecolink import deps
from ecolink.core.http import api_handler, bad_request, json_body
from ecolink.core.storage import ALLOWED_IMAGE_TYPES
from ecolink.core.validation import require_str


@api_handler
def handler(event: dict, context: Any) -> dict[str, Any]:
    data = json_body(event)
    user_id = require_str(data, "user_id", 128)
    content_type = require_str(data, "content_type", 64).lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise bad_request(f"'content_type' must be one of: {', '.join(ALLOWED_IMAGE_TYPES)}")
    return deps.get_image_store().presign_upload(user_id, content_type)
