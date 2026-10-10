"""Idempotent internal backfill and read-only reconciliation for P1B."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
import uuid

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.commercial_events import (
    TAG_EVENT_NAMES, attribution_touch_idempotency_key, managed_tag_idempotency_key,
    record_attribution_conversion_event,
    record_managed_tag_commercial_event,
)
from app.services.monitor_funnel import parse_managed_instruction
from db.models import (
    AttributionTouch, BusinessMessagingConversionMapping, CanonicalCommercialEvent,
    CommercialMetaEventLink, CRMTagEvent, MetaConversionEvent, TagCRM,
)


def _legacy_event_time(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)


async def _tag_candidates(session: AsyncSession, empresa_id: Any) -> tuple[list[tuple[CRMTagEvent, str, dict[str, Any]]], int]:
    mappings = (await session.execute(select(BusinessMessagingConversionMapping).where(
        BusinessMessagingConversionMapping.empresa_id == empresa_id,
        BusinessMessagingConversionMapping.trigger_type == "tag_applied",
        BusinessMessagingConversionMapping.ativo.is_(True),
    ))).scalars().all()
    event_by_tag = {str(row.trigger_ref): str(row.event_name or "") for row in mappings}
    rows = (await session.execute(
        select(CRMTagEvent, TagCRM.instrucao_ia)
        .join(TagCRM, and_(TagCRM.empresa_id == CRMTagEvent.empresa_id, TagCRM.id == CRMTagEvent.tag_id))
        .where(CRMTagEvent.empresa_id == empresa_id, CRMTagEvent.action == "applied")
        .order_by(CRMTagEvent.occurred_at.asc(), CRMTagEvent.id.asc())
    )).all()
    candidates: list[tuple[CRMTagEvent, str, dict[str, Any]]] = []
    ambiguous = 0
    for tag_event, instruction_raw in rows:
        instruction = parse_managed_instruction(instruction_raw) or {}
        configured = str(instruction.get("meta_event_name") or "").strip()
        mapped = event_by_tag.get(str(tag_event.tag_id), "").strip()
        if configured and mapped and configured != mapped:
            ambiguous += 1
            continue
        event_name = configured or mapped
        # Purchase is accepted only from verified payment facts, never from tags.
        if event_name in TAG_EVENT_NAMES:
            candidates.append((tag_event, event_name, instruction))
    return candidates, ambiguous


async def _legacy_classification(
    session: AsyncSession,
    event: MetaConversionEvent,
    *,
    projected_events: list[Any] | None = None,
) -> tuple[str, str, int, uuid.UUID | None]:
    occurred = _legacy_event_time(event.criado_em)
    rows = (await session.execute(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == event.empresa_id,
        CanonicalCommercialEvent.event_name == event.event_name,
        CanonicalCommercialEvent.lead_id == event.lead_id if event.lead_id else CanonicalCommercialEvent.lead_id.is_(None),
    ))).scalars().all()
    exact = []
    probable = []
    legacy_refs = {str(event.event_id or ""), str(event.trigger_source_event_id or "")}
    legacy_refs.discard("")
    matching_projected = [row for row in projected_events or [] if (
        row.empresa_id == event.empresa_id
        and row.event_name == event.event_name
        and row.lead_id == event.lead_id
    )]
    for canonical in [*rows, *matching_projected]:
        tag_ref = str(canonical.tag_event_id or "")
        evidence_ref = str((canonical.evidence_json or {}).get("tag_event_id") or "")
        source_ref = str(canonical.source_id or "")
        if legacy_refs.intersection({tag_ref, evidence_ref, source_ref}):
            exact.append(canonical)
            continue
        delta = abs((_legacy_event_time(canonical.occurred_at) - occurred).total_seconds())
        if delta <= 10 * 60:
            probable.append(canonical)
    if len(exact) == 1:
        return "LINKED_EXACT", "deterministic_event_identity", 1, exact[0].id
    if len(exact) > 1:
        return "AMBIGUOUS", "multiple_deterministic_candidates", len(exact), None
    if len(probable) == 1:
        return "LINKED_PROBABLE", "tenant_lead_event_time_window", 1, None
    if len(probable) > 1:
        return "AMBIGUOUS", "multiple_temporal_candidates", len(probable), None
    return "UNLINKED", "no_supported_commercial_fact", 0, None


async def _legacy_plan(
    session: AsyncSession,
    empresa_id: Any,
    *,
    projected_events: list[Any] | None = None,
) -> tuple[list[tuple[MetaConversionEvent, str, str, int, uuid.UUID | None]], dict[str, Any]]:
    legacy_rows = (await session.execute(select(MetaConversionEvent).where(
        MetaConversionEvent.empresa_id == empresa_id,
    ).order_by(MetaConversionEvent.criado_em.asc(), MetaConversionEvent.id.asc()))).scalars().all()
    plan = []
    classes: Counter[str] = Counter()
    by_name_status: dict[str, dict[str, int]] = {}
    for legacy in legacy_rows:
        classification, reason, count, canonical_id = await _legacy_classification(
            session, legacy, projected_events=projected_events,
        )
        plan.append((legacy, classification, reason, count, canonical_id))
        classes[classification] += 1
        key = f"{legacy.event_name}|{legacy.status}"
        item = by_name_status.setdefault(key, {"count": 0, "linked_exact": 0, "linked_probable": 0, "ambiguous": 0, "unlinked": 0})
        item["count"] += 1
        item[classification.lower()] += 1
    return plan, {"legacy_event_count": len(legacy_rows), "classifications": dict(classes), "by_event_name_status": by_name_status}


async def commercial_event_backfill(
    session: AsyncSession,
    *,
    empresa_id: Any,
    dry_run: bool = True,
) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    candidates, conflicting_configs = await _tag_candidates(session, tenant_id)
    conversion_touches = (await session.execute(select(AttributionTouch).where(
        AttributionTouch.empresa_id == tenant_id,
        AttributionTouch.event_kind.in_(["checkout_started", "order_created", "payment_approved", "refund"]),
    ).order_by(AttributionTouch.occurred_at.asc(), AttributionTouch.id.asc()))).scalars().all()
    existing_keys = set((await session.execute(select(CanonicalCommercialEvent.idempotency_key).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
    ))).scalars().all())
    projected_events = []
    would_insert = 0
    skipped_duplicate = 0
    for tag_event, event_name, _instruction in candidates:
        key = managed_tag_idempotency_key(
            empresa_id=tenant_id, lead_id=tag_event.lead_id, tag_id=tag_event.tag_id,
            event_name=event_name,
            funnel_stage_id=str(_instruction.get("funnel_stage_key") or "") or None,
            funnel_version=str(_instruction.get("funnel_version") or "") or None,
        )
        if key in existing_keys:
            skipped_duplicate += 1
        else:
            would_insert += 1
            existing_keys.add(key)
            projected_events.append(SimpleNamespace(
                id=uuid.uuid5(uuid.NAMESPACE_URL, key), empresa_id=tenant_id,
                event_name=event_name, lead_id=tag_event.lead_id,
                tag_event_id=tag_event.id, source_id=str(tag_event.id),
                evidence_json={"tag_event_id": str(tag_event.id)},
                occurred_at=tag_event.occurred_at,
            ))
    touch_would_insert = 0
    for touch in conversion_touches:
        event_name = {"checkout_started": "InitiateCheckout", "order_created": "OrderCreated", "payment_approved": "Purchase", "refund": "Refund"}[touch.event_kind]
        key = attribution_touch_idempotency_key(empresa_id=tenant_id, event_name=event_name, touch_id=touch.id)
        if key in existing_keys:
            skipped_duplicate += 1
        else:
            touch_would_insert += 1
            existing_keys.add(key)
            projected_events.append(SimpleNamespace(
                id=uuid.uuid5(uuid.NAMESPACE_URL, key), empresa_id=tenant_id,
                event_name=event_name, lead_id=touch.lead_id,
                tag_event_id=None, source_id=str(touch.id),
                evidence_json={"touch_id": str(touch.id)}, occurred_at=touch.occurred_at,
            ))
    legacy_plan, reconciliation = await _legacy_plan(
        session, tenant_id,
        projected_events=projected_events if dry_run else None,
    )
    if dry_run:
        return {
            "dry_run": True, "tag_rows": len(candidates), "touch_rows": len(conversion_touches),
            "would_insert": would_insert + touch_would_insert, "tag_would_insert": would_insert,
            "touch_would_insert": touch_would_insert, "skipped_duplicate": skipped_duplicate,
            "unresolved": conflicting_configs, "ambiguous": conflicting_configs,
            "inserted": 0, "reconciliation": reconciliation,
        }

    inserted = 0
    for tag_event, event_name, instruction in candidates:
        _event_id, created = await record_managed_tag_commercial_event(
            session, tag_event=tag_event, event_name=event_name,
            funnel_stage_id=str(instruction.get("funnel_stage_key") or "") or None,
            metadata_json={"backfill_provenance": "crm_tag_history", "funnel_version": str(instruction.get("funnel_version") or "")[:80] or None},
            is_backfilled=True,
        )
        inserted += int(created)
    for touch in conversion_touches:
        result = await record_attribution_conversion_event(session, touch=touch, is_backfilled=True)
        if result:
            inserted += int(result[1])

    # Recompute after canonical insertion so each legacy attempt is classified against all evidence.
    legacy_plan, reconciliation = await _legacy_plan(session, tenant_id)
    for legacy, classification, reason, candidate_count, canonical_id in legacy_plan:
        values = {
            "id": uuid.uuid4(), "empresa_id": tenant_id, "meta_event_id": legacy.id,
            "commercial_event_id": canonical_id if classification == "LINKED_EXACT" else None,
            "classification": classification, "match_reason": reason[:80],
            "candidate_count": candidate_count, "reconciled_at": datetime.now(timezone.utc),
        }
        await session.execute(pg_insert(CommercialMetaEventLink).values(**values).on_conflict_do_update(
            constraint="uq_commercial_event_meta_link_legacy",
            set_={key: value for key, value in values.items() if key not in {"id", "empresa_id", "meta_event_id"}},
        ))
    await session.flush()
    return {
        "dry_run": False, "tag_rows": len(candidates), "touch_rows": len(conversion_touches),
        "would_insert": would_insert + touch_would_insert, "skipped_duplicate": skipped_duplicate,
        "unresolved": conflicting_configs, "ambiguous": conflicting_configs,
        "inserted": inserted, "reconciliation": reconciliation,
    }
