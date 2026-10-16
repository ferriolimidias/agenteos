"""Tenant-bound, read-only existing-post selection and creative preview helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

from app.core.security import _jwt_secret_key
from app.services.meta.traffic_campaign_builder import (
    CAMPAIGN_OBJECTIVE,
    CONVERSION_CAMPAIGN_OBJECTIVE,
    TrafficCampaignValidationError,
    normalize_text,
)


_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
_SUPPORTED_OBJECTIVES = {CAMPAIGN_OBJECTIVE, CONVERSION_CAMPAIGN_OBJECTIVE}
_SUPPORTED_MEDIA = {"IMAGE", "VIDEO"}
_SUPPORTED_INSTAGRAM_PRODUCTS = {"FEED", "REELS"}
SELECTION_TOKEN_TTL_SECONDS = 15 * 60


class ExistingPostSelectionError(ValueError):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(text):
        raise ExistingPostSelectionError(f"{field.upper()}_INVALID")
    return text


def issue_existing_post_selection_token(
    *,
    empresa_id: Any,
    ad_account_id: Any,
    page_id: Any,
    instagram_account_id: Any | None,
    platform: str,
    source_id: Any,
    media_type: str,
    media_product_type: str | None,
    now: int | None = None,
) -> str:
    platform_value = str(platform or "").strip().lower()
    if platform_value not in {"facebook", "instagram"}:
        raise ExistingPostSelectionError("PLATFORM_INVALID")
    created = int(time.time() if now is None else now)
    claims = {
        "v": 1,
        "tenant_id": str(empresa_id),
        "ad_account_id": _safe_id(ad_account_id, "ad_account_id"),
        "page_id": _safe_id(page_id, "page_id"),
        "instagram_account_id": _safe_id(instagram_account_id, "instagram_account_id") if instagram_account_id else None,
        "platform": platform_value,
        "source_id": _safe_id(source_id, "source_id"),
        "media_type": str(media_type or "UNKNOWN").upper()[:32],
        "media_product_type": str(media_product_type or "").upper()[:32],
        "iat": created,
        "exp": created + SELECTION_TOKEN_TTL_SECONDS,
    }
    encoded = _b64url(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_jwt_secret_key().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64url(signature)}"


def verify_existing_post_selection_token(
    token: Any, *, empresa_id: Any, now: int | None = None,
) -> dict[str, Any]:
    raw = str(token or "").strip()
    try:
        encoded, supplied_signature = raw.split(".", 1)
        expected = hmac.new(_jwt_secret_key().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64url(supplied_signature)):
            raise ExistingPostSelectionError("SELECTION_TOKEN_INVALID")
        claims = json.loads(_unb64url(encoded).decode("utf-8"))
    except ExistingPostSelectionError:
        raise
    except Exception as exc:
        raise ExistingPostSelectionError("SELECTION_TOKEN_INVALID") from exc
    current = int(time.time() if now is None else now)
    if not isinstance(claims, dict) or claims.get("v") != 1:
        raise ExistingPostSelectionError("SELECTION_TOKEN_INVALID")
    if str(claims.get("tenant_id") or "") != str(empresa_id):
        raise ExistingPostSelectionError("SELECTION_TENANT_MISMATCH")
    if int(claims.get("exp") or 0) <= current:
        raise ExistingPostSelectionError("SELECTION_TOKEN_EXPIRED")
    for key in ("ad_account_id", "page_id", "source_id"):
        claims[key] = _safe_id(claims.get(key), key)
    if claims.get("instagram_account_id"):
        claims["instagram_account_id"] = _safe_id(claims["instagram_account_id"], "instagram_account_id")
    if claims.get("platform") not in {"facebook", "instagram"}:
        raise ExistingPostSelectionError("PLATFORM_INVALID")
    return claims


def normalize_existing_post_media_type(platform: str, post: dict[str, Any]) -> tuple[str, str | None]:
    if platform == "instagram":
        media_type = str(post.get("media_type") or "UNKNOWN").strip().upper()
        product = str(post.get("media_product_type") or "").strip().upper() or None
        return media_type, product
    attachments = post.get("attachments") if isinstance(post.get("attachments"), dict) else {}
    data = attachments.get("data") if isinstance(attachments.get("data"), list) else []
    first = data[0] if data and isinstance(data[0], dict) else {}
    raw = str(first.get("media_type") or "").strip().lower()
    if raw in {"video", "video_inline"}:
        return "VIDEO", None
    if raw in {"album", "multi_share"} or len(data) > 1:
        return "CAROUSEL_ALBUM", None
    if raw in {"photo", "image"} or post.get("full_picture"):
        return "IMAGE", None
    return "UNKNOWN", None


def existing_post_preview_compatibility(
    *, platform: str, source_id: str, page_id: str, media_type: str,
    media_product_type: str | None, objective: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if objective not in _SUPPORTED_OBJECTIVES:
        reasons.append("CAMPAIGN_OBJECTIVE_UNSUPPORTED_BY_CURRENT_BUILDER")
    if media_type not in _SUPPORTED_MEDIA:
        reasons.append("MEDIA_FORMAT_UNSUPPORTED_BY_CURRENT_SINGLE_MEDIA_BUILDER")
    if platform == "instagram" and media_product_type not in _SUPPORTED_INSTAGRAM_PRODUCTS:
        reasons.append("INSTAGRAM_PLACEMENT_NOT_VERIFIED_FOR_THIS_BUILDER")
    if platform == "facebook" and not source_id.startswith(f"{page_id}_"):
        reasons.append("FACEBOOK_PAGE_POST_ID_SHAPE_UNVERIFIED")
    return {
        "status": "preliminary_compatible" if not reasons else "not_compatible",
        "reasons": reasons,
        "format_supported_by_current_builder": media_type in _SUPPORTED_MEDIA,
        "objective_supported_by_current_builder": objective in _SUPPORTED_OBJECTIVES,
        "meta_acceptance_verified": False,
        "live_creation_allowed": False,
    }


def build_existing_post_creative_preview(
    *,
    name: str,
    ad_account_id: str,
    page_id: str,
    platform: str,
    source_id: str,
    instagram_account_id: str | None,
    media_type: str,
    media_product_type: str | None,
    objective: str,
) -> dict[str, Any]:
    platform_value = str(platform or "").lower()
    objective_value = str(objective or "").strip().upper()
    compatibility = existing_post_preview_compatibility(
        platform=platform_value,
        source_id=source_id,
        page_id=page_id,
        media_type=media_type,
        media_product_type=media_product_type,
        objective=objective_value,
    )
    if compatibility["status"] != "preliminary_compatible":
        raise TrafficCampaignValidationError("EXISTING_POST_NOT_COMPATIBLE_FOR_PREVIEW")

    creative_payload: dict[str, Any] = {"name": normalize_text(name, "creative_name")}
    if platform_value == "facebook":
        # Facebook Page post IDs are not Instagram media IDs or comment IDs.
        creative_payload["object_story_id"] = _safe_id(source_id, "post_id")
        id_mapping = {"post_id": source_id, "object_story_id": source_id, "media_id": None, "creative_id": None, "comment_id": None}
    else:
        if not instagram_account_id:
            raise ExistingPostSelectionError("INSTAGRAM_ACCOUNT_NOT_LINKED")
        # Meta's Marketing SDK models this as a top-level AdCreative field.
        # This payload is preview-only and is never sent to Graph in this release.
        creative_payload["source_instagram_media_id"] = _safe_id(source_id, "media_id")
        creative_payload["instagram_user_id"] = _safe_id(instagram_account_id, "instagram_account_id")
        id_mapping = {"post_id": None, "object_story_id": None, "media_id": source_id, "source_instagram_media_id": source_id, "creative_id": None, "comment_id": None}
    return {
        "state": "simulated",
        "meta_write_performed": False,
        "ad_account_id": _safe_id(ad_account_id, "ad_account_id"),
        "page_id": _safe_id(page_id, "page_id"),
        "instagram_account_id": instagram_account_id,
        "objective": objective_value,
        "format": media_type,
        "media_product_type": media_product_type,
        "id_mapping": id_mapping,
        "compatibility": compatibility,
        "creative_payload_preview": creative_payload,
        "note": "Payload local de prévia; a aceitação da Marketing API depende de validação Meta após autorização separada de criação real.",
    }
