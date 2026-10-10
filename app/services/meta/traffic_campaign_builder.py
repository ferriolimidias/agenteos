from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


CAMPAIGN_OBJECTIVE = "OUTCOME_LEADS"
CONVERSION_CAMPAIGN_OBJECTIVE = "OUTCOME_SALES"
CAMPAIGN_BUYING_TYPE = "AUCTION"
CAMPAIGN_ADSET_BUDGET_SHARING_ENABLED = False
ADSET_OPTIMIZATION_GOAL = "CONVERSATIONS"
ADSET_CONVERSION_OPTIMIZATION_GOALS = {
    "purchase": "MESSAGING_PURCHASE_CONVERSION",
    "quality_lead": "QUALITY_LEAD",
    "appointment": "MESSAGING_APPOINTMENT_CONVERSION",
}
ADSET_BILLING_EVENT = "IMPRESSIONS"
ADSET_DESTINATION_TYPE = "WHATSAPP"
ADSET_BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"
FORCED_PAUSED_STATUS = "PAUSED"
AD_CALL_TO_ACTION = "WHATSAPP_MESSAGE"
GENDER_MEN = 1
GENDER_WOMEN = 2


class TrafficCampaignValidationError(ValueError):
    pass


def normalize_text(value: Any, field: str, *, max_length: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise TrafficCampaignValidationError(f"{field.upper()}_REQUIRED")
    return text[:max_length]


def empresa_slug(value: Any, *, max_length: int = 34) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.upper()).strip("-")
    text = re.sub(r"-+", "-", text)
    return (text or "EMPRESA")[:max_length].strip("-") or "EMPRESA"


def short_empresa_id(value: Any) -> str:
    raw = str(value or "").replace("-", "").strip().upper()
    if not raw:
        raise TrafficCampaignValidationError("EMPRESA_ID_REQUIRED")
    return raw[:8]


def generated_object_name(company_name: Any, empresa_id: Any, object_type: str, sequence: int, *, ad_sequence: int | None = None, today: date | None = None) -> str:
    kind = normalize_text(object_type, "object_type", max_length=20).upper()
    if kind not in {"CAMPAIGN", "ADSET", "CREATIVE", "AD"}:
        raise TrafficCampaignValidationError("OBJECT_TYPE_INVALID")
    suffix = f"{int(sequence):02d}" if ad_sequence is None else f"{int(sequence):02d}-{int(ad_sequence):02d}"
    return f"AOS | {empresa_slug(company_name)} | {short_empresa_id(empresa_id)} | {kind} | {(today or date.today()).strftime('%Y%m%d')} | {suffix}"


def brl_to_meta_cents(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        raise TrafficCampaignValidationError("DAILY_BUDGET_REQUIRED")
    normalized = re.sub(r"[^0-9,.-]", "", raw).replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise TrafficCampaignValidationError("DAILY_BUDGET_INVALID") from exc
    if amount <= 0:
        raise TrafficCampaignValidationError("DAILY_BUDGET_INVALID")
    cents = int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise TrafficCampaignValidationError("DAILY_BUDGET_INVALID")
    return cents


def build_brazil_targeting() -> dict[str, Any]:
    return {
        "geo_locations": {
            "countries": ["BR"],
            "location_types": ["home", "recent"],
        },
        "targeting_automation": {
            "advantage_audience": 1,
        },
    }


def build_radius_targeting(latitude: Any, longitude: Any, radius_km: Any) -> dict[str, Any]:
    try:
        lat = Decimal(str(latitude))
        lng = Decimal(str(longitude))
        radius = Decimal(str(radius_km))
    except (InvalidOperation, ValueError) as exc:
        raise TrafficCampaignValidationError("RADIUS_TARGETING_INVALID") from exc
    if not (Decimal("-90") <= lat <= Decimal("90")) or not (Decimal("-180") <= lng <= Decimal("180")) or radius <= 0:
        raise TrafficCampaignValidationError("RADIUS_TARGETING_INVALID")
    return {
        "geo_locations": {
            "custom_locations": [
                {
                    "latitude": float(lat),
                    "longitude": float(lng),
                    "radius": float(radius),
                    "distance_unit": "kilometer",
                    "country": "BR",
                }
            ],
            "location_types": ["home", "recent"],
        },
        "targeting_automation": {
            "advantage_audience": 1,
            "individual_setting": {
                "age": 1,
                "gender": 1,
                "geo": 0,
            },
        },
    }


def build_targeting(*, location_mode: str, latitude: Any = None, longitude: Any = None, radius_km: Any = None, gender_mode: str = "all") -> dict[str, Any]:
    mode = str(location_mode or "").strip().lower()
    gender = str(gender_mode or "all").strip().lower()
    if mode not in {"brazil", "radius"}:
        raise TrafficCampaignValidationError("LOCATION_MODE_INVALID")
    if gender not in {"all", "women", "men"}:
        raise TrafficCampaignValidationError("GENDER_MODE_INVALID")
    targeting = build_brazil_targeting() if mode == "brazil" else build_radius_targeting(latitude, longitude, radius_km)
    if gender != "all":
        targeting["genders"] = [GENDER_WOMEN if gender == "women" else GENDER_MEN]
    return targeting


def build_campaign_payload(name: str, *, objective: str = CAMPAIGN_OBJECTIVE) -> dict[str, Any]:
    return {
        "name": normalize_text(name, "campaign_name"),
        "objective": normalize_text(objective, "campaign_objective", max_length=80),
        "buying_type": CAMPAIGN_BUYING_TYPE,
        "status": FORCED_PAUSED_STATUS,
        "special_ad_categories": [],
        "is_adset_budget_sharing_enabled": CAMPAIGN_ADSET_BUDGET_SHARING_ENABLED,
    }


def build_adset_payload(
    *,
    name: str,
    campaign_id: str,
    daily_budget_cents: int,
    page_id: str,
    phone_number: str,
    targeting: dict[str, Any],
    optimization_goal: str = ADSET_OPTIMIZATION_GOAL,
    billing_event: str = ADSET_BILLING_EVENT,
    destination_type: str = ADSET_DESTINATION_TYPE,
    promoted_object: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if int(daily_budget_cents) <= 0:
        raise TrafficCampaignValidationError("DAILY_BUDGET_INVALID")
    return {
        "name": normalize_text(name, "adset_name"),
        "campaign_id": normalize_text(campaign_id, "campaign_id"),
        "status": FORCED_PAUSED_STATUS,
        "optimization_goal": normalize_text(optimization_goal, "optimization_goal", max_length=80),
        "billing_event": normalize_text(billing_event, "billing_event", max_length=80),
        "bid_strategy": ADSET_BID_STRATEGY,
        "daily_budget": str(int(daily_budget_cents)),
        "destination_type": normalize_text(destination_type, "destination_type", max_length=80),
        "promoted_object": promoted_object
        if promoted_object is not None
        else {
            "page_id": normalize_text(page_id, "page_id"),
            "smart_pse_enabled": False,
            "whatsapp_phone_number": normalize_text(phone_number, "whatsapp_phone_number"),
        },
        "attribution_spec": [
            {
                "event_type": "CLICK_THROUGH",
                "window_days": 1,
            }
        ],
        "targeting": targeting,
    }


def build_creative_payload(
    *,
    name: str,
    page_id: str,
    primary_text: str,
    headline: str,
    description: str | None,
    phone_number_id: str,
    media_type: str,
    media_reference: str,
    instagram_account_id: str | None = None,
) -> dict[str, Any]:
    media_kind = str(media_type or "").strip().lower()
    if media_kind not in {"image", "video"}:
        raise TrafficCampaignValidationError("MEDIA_TYPE_INVALID")
    story: dict[str, Any] = {"page_id": normalize_text(page_id, "page_id")}
    if instagram_account_id:
        story["instagram_user_id"] = normalize_text(instagram_account_id, "instagram_account_id")
    normalize_text(phone_number_id, "phone_number_id")
    cta = {"type": AD_CALL_TO_ACTION, "value": {"app_destination": "WHATSAPP"}}
    if media_kind == "image":
        story["link_data"] = {"image_hash": normalize_text(media_reference, "image_hash"), "message": normalize_text(primary_text, "primary_text"), "name": normalize_text(headline, "headline"), "call_to_action": cta}
        if description:
            story["link_data"]["description"] = str(description).strip()[:240]
    else:
        story["video_data"] = {"video_id": normalize_text(media_reference, "video_id"), "message": normalize_text(primary_text, "primary_text"), "title": normalize_text(headline, "headline"), "call_to_action": cta}
        if description:
            story["video_data"]["description"] = str(description).strip()[:240]
    return {"name": normalize_text(name, "creative_name"), "object_story_spec": story}


def build_ad_payload(*, name: str, adset_id: str, creative_id: str) -> dict[str, Any]:
    return {"name": normalize_text(name, "ad_name"), "adset_id": normalize_text(adset_id, "adset_id"), "creative": {"creative_id": normalize_text(creative_id, "creative_id")}, "status": FORCED_PAUSED_STATUS}


def creation_idempotency_key(empresa_id: Any, payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{empresa_id}:{stable}".encode("utf-8")).hexdigest()
    return f"traffic_meta_campaign_adset:{digest}"
