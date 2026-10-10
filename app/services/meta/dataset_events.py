"""Single Dataset API event builder; it never logs raw CTWA or tokens."""
from __future__ import annotations

from typing import Any

from app.services.meta.conversion_credentials import ResolvedMetaCredential

BUSINESS_MESSAGING_WHATSAPP = "business_messaging_whatsapp"
BUSINESS_MESSAGING_EVENTS = {
    "Purchase", "LeadSubmitted", "InitiateCheckout", "AddToCart", "ViewContent", "OrderCreated", "OrderShipped", "OrderDelivered", "OrderCanceled", "OrderReturned", "CartAbandoned", "QualifiedLead", "RatingProvided", "ReviewProvided",
}


class MetaConversionPayloadValidationError(ValueError):
    """A local contract failure that must never reach the Graph API."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def validate_business_messaging_event_payload(payload: dict[str, Any]) -> None:
    """Validate the active Business Messaging Dataset API contract locally."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise MetaConversionPayloadValidationError("META_CONVERSION_DATA_REQUIRED", "data must be a non-empty list")

    for item in payload["data"]:
        if not isinstance(item, dict):
            raise MetaConversionPayloadValidationError("META_CONVERSION_EVENT_INVALID", "event must be an object")
        event_name = str(item.get("event_name") or "").strip()
        if event_name not in BUSINESS_MESSAGING_EVENTS:
            raise MetaConversionPayloadValidationError("META_CONVERSION_EVENT_NAME_INVALID", "event_name is not allowed")
        if not isinstance(item.get("event_time"), int) or item["event_time"] <= 0:
            raise MetaConversionPayloadValidationError("META_CONVERSION_EVENT_TIME_INVALID", "event_time must be positive epoch seconds")
        if not str(item.get("event_id") or "").strip():
            raise MetaConversionPayloadValidationError("META_CONVERSION_EVENT_ID_REQUIRED", "event_id is required")
        if item.get("action_source") != "business_messaging":
            raise MetaConversionPayloadValidationError("META_CONVERSION_ACTION_SOURCE_INVALID", "business_messaging is required")
        if item.get("messaging_channel") != "whatsapp":
            raise MetaConversionPayloadValidationError("META_CONVERSION_MESSAGING_CHANNEL_INVALID", "whatsapp is required")
        user_data = item.get("user_data")
        if not isinstance(user_data, dict) or not str(user_data.get("whatsapp_business_account_id") or "").strip():
            raise MetaConversionPayloadValidationError("META_CONVERSION_USER_DATA_REQUIRED", "whatsapp_business_account_id is required")
        if not str(user_data.get("ctwa_clid") or "").strip():
            raise MetaConversionPayloadValidationError("META_CONVERSION_CTWA_REQUIRED", "ctwa_clid is required")

        custom_data = item.get("custom_data")
        if custom_data is not None and not isinstance(custom_data, dict):
            raise MetaConversionPayloadValidationError("META_CONVERSION_CUSTOM_DATA_INVALID", "custom_data must be an object")
        if not isinstance(custom_data, dict):
            custom_data = {}
        currency = str(custom_data.get("currency") or "").strip().upper()
        if event_name == "OrderCreated" and len(currency) != 3:
            raise MetaConversionPayloadValidationError(
                "META_CONVERSION_CURRENCY_REQUIRED",
                "currency is required for OrderCreated",
            )
        if "value" in custom_data and not currency:
            raise MetaConversionPayloadValidationError(
                "META_CONVERSION_VALUE_CURRENCY_REQUIRED",
                "currency is required when custom_data.value is present",
            )


def build_business_messaging_event(*, event_name: str, event_id: str, credential: ResolvedMetaCredential, ctwa_clid: str, custom_data: dict[str, Any], partner_agent: str | None = None, event_time: int) -> dict[str, Any]:
    if event_name not in BUSINESS_MESSAGING_EVENTS:
        raise ValueError("business_messaging_event_not_allowed")
    if not ctwa_clid:
        raise ValueError("ctwa_clid_required")
    item: dict[str, Any] = {
        "event_name": event_name,
        "event_time": int(event_time),
        "event_id": event_id,
        "action_source": "business_messaging",
        "messaging_channel": "whatsapp",
        "user_data": {"whatsapp_business_account_id": credential.waba_id, "ctwa_clid": ctwa_clid},
    }
    if custom_data:
        item["custom_data"] = custom_data
    if partner_agent:
        item["partner_agent"] = partner_agent
    return {"data": [item]}
