from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.conversation_window import evaluate_conversation_window
from app.services.followup_rules import (
    FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
    FOLLOWUP_MESSAGE_MODE_FIXED_TEXT,
    FOLLOWUP_MESSAGE_MODE_META_TEMPLATE,
    FollowupRuleSpec,
)


FREEFORM_MESSAGE_MODES = {
    FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
    FOLLOWUP_MESSAGE_MODE_FIXED_TEXT,
}


@dataclass(frozen=True)
class FollowupDeliveryDecision:
    eligible: bool
    reason: str
    next_attempt_at: datetime | None = None
    business_hours_allowed: bool = True
    csw_active: bool = False
    csw_expires_at: datetime | None = None
    fep_active: bool | None = None
    fep_expires_at: datetime | None = None
    template_required: bool = False
    send_mode: str = "fixed_text"


def _to_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    aware = _to_aware_utc(value)
    if aware is None:
        return None
    return aware.replace(tzinfo=None)


def _parse_hhmm(value: str | None) -> time | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return time.fromisoformat(text[:8]).replace(second=0, microsecond=0)
    except ValueError:
        return None


def _business_hours_decision(
    spec: FollowupRuleSpec,
    *,
    now: datetime,
) -> tuple[bool, datetime | None]:
    if not spec.send_window_enabled:
        return True, None

    start = _parse_hhmm(spec.send_window_start)
    end = _parse_hhmm(spec.send_window_end)
    if start is None or end is None:
        return True, None

    try:
        tz = ZoneInfo(str(spec.timezone or "UTC"))
    except Exception:
        tz = ZoneInfo("UTC")

    now_local = (_to_aware_utc(now) or datetime.now(timezone.utc)).astimezone(tz)
    current = now_local.time().replace(second=0, microsecond=0)

    if start <= end:
        if start <= current <= end:
            return True, None
        target_date = now_local.date() if current < start else (now_local + timedelta(days=1)).date()
    else:
        if current >= start or current <= end:
            return True, None
        target_date = now_local.date()

    next_local = datetime.combine(target_date, start, tzinfo=tz)
    return False, next_local.astimezone(timezone.utc).replace(tzinfo=None)


def evaluate_followup_delivery(
    *,
    spec: FollowupRuleSpec,
    conversation: Any,
    execution: Any,
    provider: str,
    channel: str,
    now: datetime | None = None,
) -> FollowupDeliveryDecision:
    now = now or datetime.utcnow()
    send_mode = str(spec.message_mode or FOLLOWUP_MESSAGE_MODE_FIXED_TEXT)

    hours_allowed, next_attempt = _business_hours_decision(spec, now=now)
    window = evaluate_conversation_window(
        conversation=conversation,
        provider=provider,
        channel=channel,
        now=now,
    )
    csw_reference = _to_aware_utc(window.reference_at)
    csw_expires = csw_reference + timedelta(hours=24) if csw_reference is not None else None
    csw_active = window.state == "open"

    fep_expires_at = _to_naive_utc(getattr(execution, "ad_entry_expires_at", None))
    fep_active: bool | None
    if fep_expires_at is None:
        fep_active = None
    else:
        fep_active = now <= fep_expires_at

    template_required = send_mode != FOLLOWUP_MESSAGE_MODE_META_TEMPLATE and not csw_active

    if not hours_allowed:
        return FollowupDeliveryDecision(
            eligible=False,
            reason="outside_business_hours",
            next_attempt_at=next_attempt,
            business_hours_allowed=False,
            csw_active=csw_active,
            csw_expires_at=_to_naive_utc(csw_expires),
            fep_active=fep_active,
            fep_expires_at=fep_expires_at,
            template_required=template_required,
            send_mode=send_mode,
        )

    if spec.limit_to_free_entry_window and fep_active is False and send_mode in FREEFORM_MESSAGE_MODES:
        return FollowupDeliveryDecision(
            eligible=False,
            reason="free_entry_point_expired",
            business_hours_allowed=True,
            csw_active=csw_active,
            csw_expires_at=_to_naive_utc(csw_expires),
            fep_active=fep_active,
            fep_expires_at=fep_expires_at,
            template_required=template_required,
            send_mode=send_mode,
        )

    if send_mode in FREEFORM_MESSAGE_MODES and not csw_active:
        return FollowupDeliveryDecision(
            eligible=False,
            reason="customer_service_window_expired",
            business_hours_allowed=True,
            csw_active=False,
            csw_expires_at=_to_naive_utc(csw_expires),
            fep_active=fep_active,
            fep_expires_at=fep_expires_at,
            template_required=True,
            send_mode=send_mode,
        )

    return FollowupDeliveryDecision(
        eligible=True,
        reason="eligible",
        business_hours_allowed=True,
        csw_active=csw_active,
        csw_expires_at=_to_naive_utc(csw_expires),
        fep_active=fep_active,
        fep_expires_at=fep_expires_at,
        template_required=send_mode == FOLLOWUP_MESSAGE_MODE_META_TEMPLATE,
        send_mode=send_mode,
    )
