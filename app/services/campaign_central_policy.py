from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


OFFICIAL_POLICY_VERSION = "meta_official_configurable_2026_07_21_publicos_followups"


@dataclass(frozen=True)
class PolicyInput:
    provider: str
    channel: str
    origin: str | None = None
    country: str | None = None
    category: str | None = None
    execution_at: datetime | None = None
    last_inbound_at: datetime | None = None
    ad_click_at: datetime | None = None
    policy_version: str | None = None


def _hours_since(reference: datetime | None, now: datetime) -> float | None:
    if not reference:
        return None
    return max(0.0, (now - reference).total_seconds() / 3600)


def evaluate_message_policy(policy_input: PolicyInput | dict[str, Any]) -> dict[str, Any]:
    if isinstance(policy_input, dict):
        data = dict(policy_input)
        policy_input = PolicyInput(**data)

    now = policy_input.execution_at or datetime.utcnow()
    provider = str(policy_input.provider or "").lower()
    channel = str(policy_input.channel or "").lower()
    origin = str(policy_input.origin or "unknown").lower()
    category = str(policy_input.category or "unknown").lower()
    country = str(policy_input.country or "unknown").upper()

    evidence = [
        "Meta official WhatsApp Business Platform docs were checked on 2026-07-21.",
        "Meta developer pages returned rate-limit/login barriers for some current pages; unconfirmed items remain configurable.",
    ]

    known = provider in {"meta", "meta_whatsapp", "meta_official", "whatsapp_meta_official"} and channel == "whatsapp"
    service_hours = _hours_since(policy_input.last_inbound_at, now)
    ad_hours = _hours_since(policy_input.ad_click_at, now)

    service_window_state = "unknown"
    free_window_state = "unknown"
    ad_entry_window = "unknown"
    message_mode = "unknown"
    template_requirement = "unknown"
    decision = "unknown"
    reason = "policy_not_confirmed"

    if known:
        service_window_state = "probably_open" if service_hours is not None and service_hours <= 24 else "probably_closed"
        if origin in {"ctwa", "click_to_whatsapp_ad", "ad"} and ad_hours is not None:
            ad_entry_window = "active" if ad_hours <= 72 else "expired"
        elif origin in {"ctwa", "click_to_whatsapp_ad", "ad"}:
            ad_entry_window = "unknown"
        else:
            ad_entry_window = "not_applicable"
        free_window_state = "probably_open" if ad_entry_window == "active" else "not_estimated"
        template_requirement = "probably_required" if service_window_state != "probably_open" else "not_required_for_free_form"
        message_mode = "free_form_allowed" if service_window_state == "probably_open" else "template_required"
        decision = "free_form_probably_allowed" if message_mode == "free_form_allowed" else "template_probably_required"
        reason = "official_window_configurable_estimate"

    cost_state = "unknown"
    if category in {"marketing", "utility", "authentication", "service"}:
        cost_state = "rate_required"
    if free_window_state == "probably_open":
        cost_state = "estimated_free"
    elif cost_state == "rate_required":
        cost_state = "estimated_chargeable"

    return {
        "decision": decision,
        "reason": reason,
        "policy_version": policy_input.policy_version or OFFICIAL_POLICY_VERSION,
        "provider": provider,
        "channel": channel,
        "country": country,
        "category": category,
        "reference_timestamp": now.isoformat(),
        "free_window_state": free_window_state,
        "ad_entry_window": ad_entry_window,
        "message_mode": message_mode,
        "service_window_state": service_window_state,
        "template_requirement": template_requirement,
        "template_allowed": known,
        "cost_estimation_state": cost_state,
        "policy_known": known,
        "evidence": evidence,
        "effective_rules": {
            "service_window_hours": {"value": 24, "confirmed": False, "configurable": True},
            "ad_free_window_hours": {"value": 72, "confirmed": False, "configurable": True},
            "pricing_model": {"value": "per_message_or_category_rate", "confirmed": False, "configurable": True},
        },
    }


def estimate_cost(
    *,
    rate: float | None,
    currency: str | None,
    free: bool,
    charged: bool | None = None,
    source: str = "policy_rate",
) -> dict[str, Any]:
    if free:
        return {"status": "free", "estimated": 0, "derived": None, "confirmed": None, "currency": currency, "source": source}
    if rate is None:
        return {"status": "unknown", "estimated": None, "derived": None, "confirmed": None, "currency": currency, "source": "missing_rate"}
    if charged is True:
        return {"status": "derived", "estimated": rate, "derived": rate, "confirmed": None, "currency": currency, "source": source}
    return {"status": "estimated", "estimated": rate, "derived": None, "confirmed": None, "currency": currency, "source": source}
