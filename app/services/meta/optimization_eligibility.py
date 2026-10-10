"""Tenant-scoped Business Messaging optimization lifecycle.

Infrastructure readiness and Meta delivery eligibility are intentionally
separate. Meta may withhold a down-funnel goal while the integration is
healthy and signal volume is still accumulating.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MetaConversionEvent


PURCHASE_EVENT = "Purchase"
QUALIFIED_LEAD_EVENT = "QualifiedLead"
SIGNAL_EVENTS = ("LeadSubmitted", "QualifiedLead", "InitiateCheckout", "Purchase")
WHATSAPP_PURCHASE_ELIGIBILITY_THRESHOLD = None


def _meta_events_received(row: MetaConversionEvent) -> int:
    preview = dict(getattr(row, "response_preview", None) or {})
    body = preview.get("body") if isinstance(preview.get("body"), dict) else preview
    try:
        return int(body.get("events_received") or 0)
    except (TypeError, ValueError):
        return 0


def is_meta_accepted_event(row: MetaConversionEvent) -> bool:
    return str(getattr(row, "status", "") or "").lower() == "sent" and _meta_events_received(row) > 0


def _signal_count(rows: list[MetaConversionEvent], event_name: str) -> int:
    return sum(1 for row in rows if row.event_name == event_name and is_meta_accepted_event(row))


def _status_counts(rows: list[MetaConversionEvent]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for event_name in SIGNAL_EVENTS:
        result[event_name] = dict(Counter(str(row.status or "unknown") for row in rows if row.event_name == event_name))
    return result


def _state(*, infrastructure_ready: bool, purchase_count: int, qualified_lead_count: int, purchase_probe: str | None = None, lead_probe: str | None = None) -> str:
    if not infrastructure_ready:
        return "INFRASTRUCTURE_NOT_READY"
    if purchase_probe == "eligible":
        return "ELIGIBLE_PURCHASE"
    if lead_probe == "eligible":
        return "ELIGIBLE_LEAD"
    if purchase_count == 0 and qualified_lead_count == 0:
        return "NOT_ENOUGH_SIGNAL"
    return "SIGNAL_ACCUMULATING"


async def get_meta_optimization_eligibility(
    session: AsyncSession,
    empresa_id: Any,
    *,
    infrastructure_ready: bool,
    purchase_probe: str | None = None,
    lead_probe: str | None = None,
) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                select(MetaConversionEvent)
                .where(
                    MetaConversionEvent.empresa_id == empresa_id,
                    MetaConversionEvent.conversion_context == "business_messaging_whatsapp",
                    MetaConversionEvent.event_name.in_(SIGNAL_EVENTS),
                )
                .order_by(MetaConversionEvent.criado_em.asc())
            )
        ).scalars().all()
    )
    accepted = {event_name: _signal_count(rows, event_name) for event_name in SIGNAL_EVENTS}
    last_accepted: dict[str, Any] = {}
    for event_name in SIGNAL_EVENTS:
        values = [row.criado_em for row in rows if row.event_name == event_name and is_meta_accepted_event(row)]
        last_accepted[event_name] = max(values).isoformat() if values else None
    purchase_count = accepted[PURCHASE_EVENT]
    qualified_lead_count = accepted[QUALIFIED_LEAD_EVENT]
    state = _state(
        infrastructure_ready=infrastructure_ready,
        purchase_count=purchase_count,
        qualified_lead_count=qualified_lead_count,
        purchase_probe=purchase_probe,
        lead_probe=lead_probe,
    )
    return {
        "state": state,
        "infrastructure_ready": bool(infrastructure_ready),
        "purchase_optimization_ready": state == "ELIGIBLE_PURCHASE",
        "lead_optimization_ready": state == "ELIGIBLE_LEAD",
        "purchase_eligibility_threshold": None,
        "purchase_eligibility_threshold_source": "not hardcoded; WhatsApp threshold not established by current evidence",
        "accepted_signal_counts": accepted,
        "status_counts": _status_counts(rows),
        "last_accepted_at": last_accepted,
        "purchase_probe": purchase_probe,
        "lead_probe": lead_probe,
        "bootstrap": {
            "required": state == "NOT_ENOUGH_SIGNAL",
            "mode": "signal_accumulation" if state in {"NOT_ENOUGH_SIGNAL", "SIGNAL_ACCUMULATING"} else None,
            "conversations_product_option": False,
            "conversations_automatic_fallback": False,
        },
        "dry_run_excluded": True,
    }
