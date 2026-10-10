"""Canonical internal commercial facts, separated from Meta delivery attempts."""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AttributionTouch,
    AttributionTouchResolution,
    CanonicalCommercialEvent,
    CommercialMetaEventLink,
    CRMTagEvent,
    MetaConversionEvent,
)

logger = logging.getLogger(__name__)

COMMERCIAL_EVENT_NAMES = frozenset({
    "ViewContent", "LeadSubmitted", "QualifiedLead", "AddToCart", "InitiateCheckout",
    "OrderCreated", "CartAbandoned", "Purchase", "Refund", "OrderShipped",
    "OrderDelivered", "OrderCanceled", "OrderReturned", "RatingProvided", "ReviewProvided",
})
TAG_EVENT_NAMES = frozenset({"ViewContent", "LeadSubmitted", "QualifiedLead", "AddToCart", "CartAbandoned"})
TOUCH_EVENT_NAMES = {
    "checkout_started": "InitiateCheckout",
    "order_created": "OrderCreated",
    "payment_approved": "Purchase",
    "refund": "Refund",
}
EVENT_CATEGORY = {
    "ViewContent": "funnel", "LeadSubmitted": "lead", "QualifiedLead": "qualification",
    "AddToCart": "selection", "InitiateCheckout": "checkout", "OrderCreated": "order",
    "CartAbandoned": "abandonment", "Purchase": "purchase", "Refund": "refund",
    "OrderShipped": "fulfillment", "OrderDelivered": "fulfillment", "OrderCanceled": "fulfillment",
    "OrderReturned": "fulfillment", "RatingProvided": "feedback", "ReviewProvided": "feedback",
}
ENTRY_TOUCH_EVENTS = frozenset({"ctwa_referral", "tracked_link_click", "manual_touch", "unknown_source"})
LAST_TOUCH_EVENTS = frozenset({"ctwa_referral", "tracked_link_click", "campaign_sent", "campaign_reply", "manual_touch"})


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)


def _idempotency_key(*parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def managed_tag_idempotency_key(
    *, empresa_id: Any, lead_id: Any, tag_id: Any, event_name: str,
    funnel_stage_id: str | None = None, funnel_version: str | None = None,
) -> str:
    # Same business transition observed by Monitor, manual tag, automation or
    # retry converges to one event; tag-event IDs are evidence, not identity.
    canonical_object = str(funnel_stage_id or tag_id)
    return _idempotency_key(
        "commercial-tag-stage:v1", empresa_id, event_name, lead_id,
        canonical_object, funnel_version or "",
    )


def attribution_touch_idempotency_key(*, empresa_id: Any, event_name: str, touch_id: Any) -> str:
    return _idempotency_key("commercial-touch:v1", empresa_id, event_name, touch_id)


def _safe_id(value: Any, limit: int = 200) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        return None
    return text


def _json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Allow-list scalar evidence only; conversation text, phones and tokens never enter this store."""
    output: dict[str, Any] = {}
    if not isinstance(value, Mapping):
        return output
    for key, item in value.items():
        name = str(key or "").strip()[:64]
        lowered = name.lower()
        if not name or any(secret in lowered for secret in ("token", "secret", "password", "phone", "message_body", "conversation_text")):
            continue
        if item is None or isinstance(item, (bool, int, float)):
            output[name] = item
        elif isinstance(item, str):
            output[name] = item[:240]
        elif isinstance(item, (list, tuple)):
            output[name] = [str(value)[:160] for value in item[:20] if value is not None]
    return output


async def _touch_with_resolution(session: AsyncSession, empresa_id: Any, touch_id: Any | None):
    if not touch_id:
        return None, None
    touch_uuid = uuid.UUID(str(touch_id))
    row = (await session.execute(
        select(AttributionTouch, AttributionTouchResolution)
        .outerjoin(AttributionTouchResolution, and_(
            AttributionTouchResolution.empresa_id == AttributionTouch.empresa_id,
            AttributionTouchResolution.touch_id == AttributionTouch.id,
        ))
        .where(AttributionTouch.empresa_id == uuid.UUID(str(empresa_id)), AttributionTouch.id == touch_uuid)
    )).first()
    return (row[0], row[1]) if row else (None, None)


async def _snapshot_attribution(
    session: AsyncSession,
    *,
    empresa_id: Any,
    occurred_at: datetime,
    lead_id: Any | None,
    contact_id: Any | None,
    conversation_id: Any | None,
    preferred_last_touch_id: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tenant_id = uuid.UUID(str(empresa_id))
    scopes = []
    if lead_id:
        scopes.append(AttributionTouch.lead_id == uuid.UUID(str(lead_id)))
    if contact_id:
        scopes.append(AttributionTouch.contact_id == uuid.UUID(str(contact_id)))
    if conversation_id:
        scopes.append(AttributionTouch.conversation_id == uuid.UUID(str(conversation_id)))
    rows = []
    if scopes:
        rows = list((await session.execute(
            select(AttributionTouch, AttributionTouchResolution)
            .outerjoin(AttributionTouchResolution, and_(
                AttributionTouchResolution.empresa_id == AttributionTouch.empresa_id,
                AttributionTouchResolution.touch_id == AttributionTouch.id,
            ))
            .where(
                AttributionTouch.empresa_id == tenant_id,
                or_(*scopes),
                AttributionTouch.occurred_at <= _utc(occurred_at),
            )
            .order_by(AttributionTouch.occurred_at.asc(), AttributionTouch.created_at.asc())
        )).all())
    entry = next(((touch, resolution) for touch, resolution in rows if touch.event_kind in ENTRY_TOUCH_EVENTS), None)
    last = None
    if preferred_last_touch_id:
        last = await _touch_with_resolution(session, tenant_id, preferred_last_touch_id)
        if last[0] is None:
            last = None
    if last is None:
        last = next(((touch, resolution) for touch, resolution in reversed(rows) if touch.event_kind in LAST_TOUCH_EVENTS), None)

    def touch_snapshot(pair):
        if not pair or not pair[0]:
            return None
        touch, resolution = pair
        status = resolution.resolution_status if resolution else ("partial" if touch.source_id else "not_applicable")
        return {
            "touch_id": str(touch.id), "resolution_id": str(resolution.id) if resolution else None,
            "event_kind": touch.event_kind, "occurred_at": _utc(touch.occurred_at).isoformat(),
            "resolution_status": status,
            "ad_id": resolution.ad_id if resolution else None,
            "adset_id": resolution.adset_id if resolution else None,
            "campaign_id": resolution.campaign_id if resolution else None,
            "creative_id": resolution.creative_id if resolution else None,
            "ad_name": resolution.ad_name_snapshot if resolution else None,
            "adset_name": resolution.adset_name_snapshot if resolution else None,
            "campaign_name": resolution.campaign_name_snapshot if resolution else None,
        }

    first_value, last_value = touch_snapshot(entry), touch_snapshot(last)
    assisted = []
    if first_value and last_value:
        first_id = first_value["touch_id"]
        for touch, _resolution in rows:
            if touch.event_kind in LAST_TOUCH_EVENTS and str(touch.id) != first_id and touch.occurred_at >= entry[0].occurred_at:
                assisted.append(str(touch.id))
    snapshot = {
        "captured_at": _utc(occurred_at).isoformat(),
        "model": "last_touch",
        "first_touch": first_value,
        "last_touch": last_value,
        "assisted_touch_ids": assisted,
    }
    context = {
        "first_touch_id": uuid.UUID(first_value["touch_id"]) if first_value else None,
        "last_touch_id": uuid.UUID(last_value["touch_id"]) if last_value else None,
        "attribution_touch_id": uuid.UUID(last_value["touch_id"]) if last_value else None,
        "attribution_resolution_id": uuid.UUID(last_value["resolution_id"]) if last_value and last_value.get("resolution_id") else None,
        "attribution_status": last_value["resolution_status"] if last_value else "unknown",
        "first_campaign_id": first_value.get("campaign_id") if first_value else None,
        "first_adset_id": first_value.get("adset_id") if first_value else None,
        "first_ad_id": first_value.get("ad_id") if first_value else None,
        "last_campaign_id": last_value.get("campaign_id") if last_value else None,
        "last_adset_id": last_value.get("adset_id") if last_value else None,
        "last_ad_id": last_value.get("ad_id") if last_value else None,
    }
    return snapshot, context


async def _project_shadow_best_effort(session: AsyncSession, *, empresa_id: uuid.UUID, event_id: uuid.UUID) -> None:
    """Project after canonical persistence without coupling it to Meta delivery."""
    try:
        from app.services.meta.canonical_conversion_shadow import project_commercial_event_best_effort

        await project_commercial_event_best_effort(
            session, empresa_id=empresa_id, commercial_event_id=event_id,
        )
    except Exception as exc:
        # Never let the new, non-sending shadow path affect the durable business fact.
        logger.warning(
            "COMMERCIAL_META_SHADOW_HOOK_FAILED",
            extra={"reason_type": type(exc).__name__, "tenant_scoped": True},
        )


async def record_commercial_event(
    session: AsyncSession,
    *,
    empresa_id: Any,
    event_name: str,
    source_type: str,
    source_id: Any | None,
    occurred_at: datetime,
    lead_id: Any | None = None,
    contact_id: Any | None = None,
    conversation_id: Any | None = None,
    message_id: Any | None = None,
    tag_event_id: Any | None = None,
    funnel_stage_id: str | None = None,
    checkout_id: Any | None = None,
    payment_id: Any | None = None,
    provider: str | None = None,
    value: Any | None = None,
    currency: str | None = None,
    revenue_verified: bool = False,
    is_backfilled: bool = False,
    preferred_last_touch_id: Any | None = None,
    evidence_json: Mapping[str, Any] | None = None,
    metadata_json: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[uuid.UUID, bool]:
    name = str(event_name or "").strip()
    if name not in COMMERCIAL_EVENT_NAMES:
        raise ValueError("commercial_event_name_invalid")
    tenant_id = uuid.UUID(str(empresa_id))
    occurred = _utc(occurred_at)
    amount = Decimal(str(value)) if value is not None else None
    if amount is not None and not amount.is_finite():
        raise ValueError("commercial_event_value_invalid")
    normalized_currency = str(currency or "").strip().upper() if amount is not None else None
    if amount is not None and (len(normalized_currency or "") != 3 or not normalized_currency.isalpha()):
        raise ValueError("commercial_event_currency_invalid")
    snapshot, context = await _snapshot_attribution(
        session, empresa_id=tenant_id, occurred_at=occurred, lead_id=lead_id,
        contact_id=contact_id, conversation_id=conversation_id,
        preferred_last_touch_id=preferred_last_touch_id,
    )
    policy = "historical_no_send" if is_backfilled else ("not_applicable" if name in {"Refund", "RatingProvided", "ReviewProvided"} else "disabled")
    key = idempotency_key or _idempotency_key("commercial-event:v1", tenant_id, name, source_type, source_id)
    values = {
        "id": uuid.uuid4(), "empresa_id": tenant_id, "event_name": name,
        "event_category": EVENT_CATEGORY[name], "source_type": _safe_id(source_type, 60) or "unknown",
        "source_id": _safe_id(source_id), "provider": _safe_id(provider, 80),
        "lead_id": uuid.UUID(str(lead_id)) if lead_id else None,
        "contact_id": uuid.UUID(str(contact_id)) if contact_id else None,
        "conversation_id": uuid.UUID(str(conversation_id)) if conversation_id else None,
        "message_id": uuid.UUID(str(message_id)) if message_id else None,
        "tag_event_id": uuid.UUID(str(tag_event_id)) if tag_event_id else None,
        "funnel_stage_id": _safe_id(funnel_stage_id, 120),
        "checkout_id": _safe_id(checkout_id), "payment_id": _safe_id(payment_id),
        **context, "occurred_at": occurred, "value": amount,
        "currency": normalized_currency, "revenue_verified": bool(revenue_verified and name in {"Purchase", "Refund"}),
        "is_backfilled": bool(is_backfilled), "external_dispatch_policy": policy,
        "attribution_snapshot": snapshot,
        "evidence_json": _json_object(evidence_json), "metadata_json": _json_object(metadata_json),
        "idempotency_key": key[:128],
    }
    insert = pg_insert(CanonicalCommercialEvent).values(**values).on_conflict_do_nothing(
        constraint="uq_commercial_events_idempotency"
    ).returning(CanonicalCommercialEvent.id)
    inserted_id = await session.scalar(insert)
    if inserted_id is not None:
        await _project_shadow_best_effort(session, empresa_id=tenant_id, event_id=inserted_id)
        return inserted_id, True
    existing_id = await session.scalar(select(CanonicalCommercialEvent.id).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
        CanonicalCommercialEvent.idempotency_key == values["idempotency_key"],
    ))
    if existing_id is None:
        raise RuntimeError("commercial_event_conflict_without_existing_row")
    await _project_shadow_best_effort(session, empresa_id=tenant_id, event_id=existing_id)
    return existing_id, False


async def record_managed_tag_commercial_event(
    session: AsyncSession,
    *,
    tag_event: CRMTagEvent,
    event_name: str,
    funnel_stage_id: str | None = None,
    metadata_json: Mapping[str, Any] | None = None,
    is_backfilled: bool = False,
) -> tuple[uuid.UUID, bool]:
    if event_name not in TAG_EVENT_NAMES or tag_event.action != "applied":
        raise ValueError("managed_tag_event_not_canonical")
    return await record_commercial_event(
        session, empresa_id=tag_event.empresa_id, event_name=event_name,
        source_type="crm_tag_history", source_id=tag_event.id,
        occurred_at=tag_event.occurred_at, lead_id=tag_event.lead_id,
        contact_id=tag_event.contact_id, conversation_id=tag_event.conversation_id,
        tag_event_id=tag_event.id, funnel_stage_id=funnel_stage_id,
        is_backfilled=is_backfilled,
        evidence_json={"evidence_type": "crm_tag_event", "tag_event_id": str(tag_event.id), "source": tag_event.source},
        metadata_json=metadata_json,
        idempotency_key=managed_tag_idempotency_key(
            empresa_id=tag_event.empresa_id, lead_id=tag_event.lead_id,
            tag_id=tag_event.tag_id, event_name=event_name,
            funnel_stage_id=funnel_stage_id,
            funnel_version=str((metadata_json or {}).get("funnel_version") or "") or None,
        ),
    )


async def record_attribution_conversion_event(
    session: AsyncSession,
    *,
    touch: AttributionTouch,
    is_backfilled: bool | None = None,
) -> tuple[uuid.UUID, bool] | None:
    event_name = TOUCH_EVENT_NAMES.get(str(touch.event_kind or ""))
    if not event_name:
        return None
    touch_metadata = dict(touch.metadata_json or {})
    provenance = str(touch_metadata.get("provenance") or "")
    verified = (
        event_name in {"Purchase", "Refund"}
        and touch.touch_source == "payment_webhook"
        and provenance == "provider_verified_payment_status"
        and touch.amount is not None
        and len(str(touch.currency or "")) == 3
    )
    amount = abs(Decimal(str(touch.amount))) if verified and touch.amount is not None else None
    historical = bool(is_backfilled) if is_backfilled is not None else str(touch.touch_source or "").startswith("backfill_")
    return await record_commercial_event(
        session, empresa_id=touch.empresa_id, event_name=event_name,
        source_type="attribution_touch", source_id=touch.id,
        occurred_at=touch.occurred_at, lead_id=touch.lead_id,
        contact_id=touch.contact_id, conversation_id=touch.conversation_id,
        message_id=touch.message_id, checkout_id=touch.checkout_id,
        payment_id=touch.payment_id, provider=touch.provider,
        value=amount, currency=touch.currency if amount is not None else None,
        revenue_verified=verified, is_backfilled=historical,
        preferred_last_touch_id=touch.attribution_touch_id,
        evidence_json={"evidence_type": "attribution_conversion_fact", "touch_id": str(touch.id), "event_kind": touch.event_kind, "provider_status_verified": verified},
        metadata_json={"provenance": provenance} if provenance else {},
        idempotency_key=attribution_touch_idempotency_key(
            empresa_id=touch.empresa_id, event_name=event_name, touch_id=touch.id,
        ),
    )


async def link_legacy_meta_event_exact(
    session: AsyncSession,
    *,
    empresa_id: Any,
    meta_event_id: Any,
    commercial_event_id: Any,
    match_reason: str,
) -> None:
    tenant_id = uuid.UUID(str(empresa_id))
    legacy_id, commercial_id = uuid.UUID(str(meta_event_id)), uuid.UUID(str(commercial_event_id))
    statement = pg_insert(CommercialMetaEventLink).values(
        id=uuid.uuid4(), empresa_id=tenant_id, meta_event_id=legacy_id,
        commercial_event_id=commercial_id, classification="LINKED_EXACT",
        match_reason=_safe_id(match_reason, 80) or "exact_identity",
        candidate_count=1, reconciled_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        constraint="uq_commercial_event_meta_link_legacy",
        set_={"commercial_event_id": commercial_id, "classification": "LINKED_EXACT", "match_reason": _safe_id(match_reason, 80) or "exact_identity", "candidate_count": 1, "reconciled_at": datetime.now(timezone.utc)},
    )
    await session.execute(statement)
    await _project_shadow_best_effort(session, empresa_id=tenant_id, event_id=commercial_id)


async def meta_signal_status(session: AsyncSession, event: CanonicalCommercialEvent) -> str:
    if event.is_backfilled or event.external_dispatch_policy == "historical_no_send":
        return "META_SIGNAL_NOT_SENT"
    link = await session.scalar(select(CommercialMetaEventLink).where(
        CommercialMetaEventLink.empresa_id == event.empresa_id,
        CommercialMetaEventLink.commercial_event_id == event.id,
        CommercialMetaEventLink.classification == "LINKED_EXACT",
    ).order_by(CommercialMetaEventLink.reconciled_at.desc()))
    if link:
        legacy = await session.scalar(select(MetaConversionEvent).where(
            MetaConversionEvent.empresa_id == event.empresa_id, MetaConversionEvent.id == link.meta_event_id,
        ))
        if legacy:
            status = str(legacy.status or "").lower()
            if status in {"sent", "accepted"}:
                response = legacy.response_preview if isinstance(legacy.response_preview, dict) else {}
                body = response.get("body") if isinstance(response.get("body"), dict) else response
                received = body.get("events_received") if isinstance(body, dict) else None
                return "META_SIGNAL_ACCEPTED" if isinstance(received, (int, float)) and received > 0 else "META_SIGNAL_SENT"
            if status == "pending":
                return "META_SIGNAL_PENDING"
            if status == "failed":
                return "META_SIGNAL_FAILED"
            if status == "blocked":
                return "META_SIGNAL_BLOCKED"
    if event.external_dispatch_policy == "not_applicable":
        return "META_SIGNAL_NOT_APPLICABLE"
    return "META_SIGNAL_NOT_SENT"


def canonical_dispatch_decision(event: CanonicalCommercialEvent, *, mode: str = "disabled") -> dict[str, Any]:
    """P1B is deliberately non-sending; live cutover belongs to P1C."""
    if event.is_backfilled or event.external_dispatch_policy == "historical_no_send":
        return {"dispatch": False, "status": "META_SIGNAL_NOT_SENT", "reason": "historical_no_send"}
    normalized = str(mode or "disabled").strip().lower()
    if normalized not in {"disabled", "shadow", "live"}:
        normalized = "disabled"
    if normalized == "live":
        return {"dispatch": False, "status": "META_SIGNAL_BLOCKED", "reason": "p1c_cutover_required"}
    if event.external_dispatch_policy == "not_applicable":
        return {"dispatch": False, "status": "META_SIGNAL_NOT_APPLICABLE", "reason": "event_not_meta_applicable"}
    return {"dispatch": False, "status": "META_SIGNAL_NOT_SENT", "reason": "dispatch_disabled"}
