"""Link explicit legacy producer evidence, never business facts from HTTP responses.

No network or sender is imported. Historical guesses retain their classification.
"""
import hashlib
import uuid
from sqlalchemy import select, func, exists
from db.models import LegacyConversionDeliveryEvidence, CommercialMetaEventLink, CanonicalCommercialEvent, MetaConversionEvent


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


async def delivery_state_for_identity(session, *, event, legacy, credential):
    if legacy is None or credential is None:
        return "UNKNOWN_DELIVERY"
    # Full destination identity is required: a masked suffix is not evidence.
    rows = (await session.execute(select(LegacyConversionDeliveryEvidence).where(
        LegacyConversionDeliveryEvidence.empresa_id == event.empresa_id,
        LegacyConversionDeliveryEvidence.commercial_event_id == event.id,
        LegacyConversionDeliveryEvidence.legacy_event_id == legacy.id,
        LegacyConversionDeliveryEvidence.identity_classification == "EXACT_LINKED",
        LegacyConversionDeliveryEvidence.external_event_id_hash == _hash(legacy.event_id),
        LegacyConversionDeliveryEvidence.dataset_id_hash == _hash(credential.dataset_id),
        LegacyConversionDeliveryEvidence.waba_id_hash == _hash(credential.waba_id),
    ).order_by(LegacyConversionDeliveryEvidence.attempt_started_at.desc()))).scalars().all()
    if any(row.request_outcome == "PROVIDER_ACCEPTED" for row in rows):
        return "LEGACY_ACCEPTED"  # Endpoint acceptance, never advertising attribution.
    if any(row.delivery_uncertainty for row in rows):
        return "UNKNOWN_DELIVERY"
    if rows and all(row.request_outcome == "PROVIDER_REJECTED" for row in rows):
        return "LEGACY_FAILED"
    return "UNKNOWN_DELIVERY"


async def reconcile_legacy_delivery_links(session, *, empresa_id, limit=100):
    """Repair explicit producer links after crash; no probable -> exact approximation."""
    tenant = uuid.UUID(str(empresa_id))
    linked = exists(select(CommercialMetaEventLink.id).where(
        CommercialMetaEventLink.empresa_id == tenant,
        CommercialMetaEventLink.meta_event_id == LegacyConversionDeliveryEvidence.legacy_event_id,
        CommercialMetaEventLink.classification == "LINKED_EXACT",
    ))
    legacy_ids = list((await session.execute(select(LegacyConversionDeliveryEvidence.legacy_event_id).where(
        LegacyConversionDeliveryEvidence.empresa_id == tenant,
        LegacyConversionDeliveryEvidence.identity_classification == "EXACT_LINKED", ~linked,
    ).group_by(LegacyConversionDeliveryEvidence.legacy_event_id).having(
        func.count(func.distinct(LegacyConversionDeliveryEvidence.commercial_event_id)) == 1,
    ).order_by(
        func.min(LegacyConversionDeliveryEvidence.attempt_started_at),
    ).limit(min(max(limit, 1), 500)))).scalars().all())
    if not legacy_ids:
        return 0
    # Bound distinct unresolved legacy identities, never truncate their evidence.
    rows = (await session.execute(select(LegacyConversionDeliveryEvidence).where(
        LegacyConversionDeliveryEvidence.empresa_id == tenant,
        LegacyConversionDeliveryEvidence.identity_classification == "EXACT_LINKED",
        LegacyConversionDeliveryEvidence.legacy_event_id.in_(legacy_ids),
    ))).scalars().all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row.legacy_event_id, set()).add(row.commercial_event_id)
    repaired = 0
    for legacy_id, fact_ids in grouped.items():
        if len(fact_ids) != 1:
            continue  # Conflicting explicit identities require human investigation.
        fact_id = next(iter(fact_ids))
        fact = await session.scalar(select(CanonicalCommercialEvent).where(CanonicalCommercialEvent.empresa_id == tenant, CanonicalCommercialEvent.id == fact_id))
        legacy = await session.scalar(select(MetaConversionEvent).where(MetaConversionEvent.empresa_id == tenant, MetaConversionEvent.id == legacy_id))
        if fact is None or legacy is None or fact.event_name != legacy.event_name or not fact.lead_id or fact.lead_id != legacy.lead_id:
            continue
        existing = await session.scalar(select(CommercialMetaEventLink).where(CommercialMetaEventLink.empresa_id == tenant, CommercialMetaEventLink.meta_event_id == legacy_id))
        if existing and existing.classification == "LINKED_EXACT":
            continue  # Never replace existing exact identity or silently reinterpret it.
        from app.services.commercial_events import link_legacy_meta_event_exact
        await link_legacy_meta_event_exact(session, empresa_id=tenant, meta_event_id=legacy_id, commercial_event_id=fact_id, match_reason="durable_explicit_producer_identity")
        repaired += 1
    return repaired
