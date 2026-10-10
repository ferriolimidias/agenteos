from __future__ import annotations

import copy
import hashlib
from typing import Any


def mask_token(token: str) -> str:
    cleaned = str(token or "").strip()
    if len(cleaned) <= 10:
        return "***"
    return f"{cleaned[:6]}...{cleaned[-4:]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def sanitize_payload_for_storage(payload: dict, token: str | None = None) -> dict:
    sanitized = copy.deepcopy(payload or {})
    if not isinstance(sanitized, dict):
        return {}

    reference_hash = hash_token(token) if token else None
    masked = mask_token(token) if token else None

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for key, item in value.items():
                if key == "leadToken":
                    if masked:
                        output["leadTokenMasked"] = masked
                    if reference_hash:
                        output["tokenReferenceHash"] = reference_hash
                    continue
                output[key] = clean(item)
            return output
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    result = clean(sanitized)
    if reference_hash and "tokenReferenceHash" not in result:
        result["tokenReferenceHash"] = reference_hash
    if masked and "leadTokenMasked" not in result:
        result["leadTokenMasked"] = masked
    return result
