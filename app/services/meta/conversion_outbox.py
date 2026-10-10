"""Durable, tenant-scoped Meta conversion intents with a hard SHADOW boundary.

This module deliberately has no Meta transport/client imports. SHADOW claims
record simulations only; a separately approved cutover must add the external
dispatcher and remove the database/application LIVE gates.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    CanonicalCommercialEvent,
    CanonicalConversionDeliveryAttempt,
    CanonicalConversionOutbox,
    CanonicalMetaConversionProjection,
    CommercialMetaEventLink,
    MetaConversionEvent,
)

CANONICAL_LIVE_ENABLED = False
OUTBOX_PAYLOAD_VERSION = 1
_SAFE_ERROR_CLASSES = frozenset({
    "Exception", "TimeoutError", "ConnectionError", "RuntimeError", "ValueError",
    "DatabaseError", "DBAPIError", "IntegrityError", "SimulatedError", "MockError",
})
_outbox_logger = logging.getLogger("uvicorn.error")


def canonical_outbox_idempotency_key(canonical_event_id: str) -> str:
    return f"canonical:{str(canonical_event_id)}"


def _safe_error_class(value: str | None) -> str | None:
    candidate = str(value or "").strip().rsplit(".", 1)[-1]
    return candidate if candidate in _SAFE_ERROR_CLASSES else ("simulated_error" if value else None)


def canonical_dedup_decision(
    *,
    historical_no_send: bool,
    projection_valid: bool,
    legacy_reference_classification: str,
    legacy_delivery_state: str,
) -> str:
    if historical_no_send:
        return "HISTORICAL_NO_SEND"
    if not projection_valid:
        return "INVALID_PROJECTION"
    legacy_class = str(legacy_reference_classification or "UNLINKED").upper()
    delivery = str(legacy_delivery_state or "UNKNOWN_DELIVERY").upper()
    if legacy_class == "EXACT_LINKED" and delivery in {
        "ALREADY_DELIVERED_BY_LEGACY", "LEGACY_ACCEPTED"
    }:
        return "EXACT_LEGACY_DELIVERY"
    if legacy_class in {"PROBABLE_ONLY", "AMBIGUOUS", "LINKED_PROBABLE"}:
        return "POSSIBLE_LEGACY_DUPLICATE"
    if legacy_class == "EXACT_LINKED":
        return "UNKNOWN_LEGACY_DELIVERY"
    if legacy_class == "UNLINKED":
        return "NO_LEGACY_REFERENCE"
    if delivery in {"UNKNOWN_DELIVERY", "LEGACY_FAILED"}:
        return "UNKNOWN_LEGACY_DELIVERY"
    return "SAFE_SHADOW_ONLY"


def _effective_dedup_decision(
    *,
    stored_decision: str | None,
    legacy_reference_classification: str | None,
    legacy_delivery_state: str | None,
) -> str:
    stored = str(stored_decision or "").upper()
    if stored:
        return stored
    legacy_class = str(legacy_reference_classification or "UNLINKED").upper()
    delivery = str(legacy_delivery_state or "UNKNOWN_DELIVERY").upper()
    if legacy_class in {"PROBABLE_ONLY", "AMBIGUOUS", "LINKED_PROBABLE"}:
        return "POSSIBLE_LEGACY_DUPLICATE"
    if legacy_class == "EXACT_LINKED":
        return "EXACT_LEGACY_DELIVERY" if delivery in {
            "ALREADY_DELIVERED_BY_LEGACY", "LEGACY_ACCEPTED"
        } else "UNKNOWN_LEGACY_DELIVERY"
    if legacy_class == "UNLINKED":
        return "NO_LEGACY_REFERENCE"
    if delivery in {"UNKNOWN_DELIVERY", "LEGACY_FAILED"}:
        return "UNKNOWN_LEGACY_DELIVERY"
    return "SAFE_SHADOW_ONLY"


def _is_duplicate_risk(
    *,
    stored_decision: str | None,
    legacy_reference_classification: str | None,
    legacy_delivery_state: str | None,
) -> bool:
    return _effective_dedup_decision(
        stored_decision=stored_decision,
        legacy_reference_classification=legacy_reference_classification,
        legacy_delivery_state=legacy_delivery_state,
    ) in {
        "POSSIBLE_LEGACY_DUPLICATE", "UNKNOWN_LEGACY_DELIVERY", "EXACT_LEGACY_DELIVERY"
    }


def future_external_delivery_permitted(
    *,
    dedup_decision: str,
    historical_no_send: bool,
    tenant_enabled: bool,
    event_enabled: bool,
) -> bool:
    """Hard release gate. It remains false until a separate LIVE cutover."""
    return bool(
        CANONICAL_LIVE_ENABLED
        and tenant_enabled
        and event_enabled
        and not historical_no_send
        and str(dedup_decision or "").upper() == "SAFE_SHADOW_ONLY"
    )


def classify_external_delivery_outcome(
    *,
    phase: str,
    http_status: int | None = None,
    valid_provider_ack: bool = False,
) -> str:
    """Pure future-cutover classifier; it performs no network operation."""
    phase = str(phase or "").strip().lower()
    if phase == "before_external_call":
        return "RETRY"
    if phase == "timeout_after_external_call":
        return "UNKNOWN_DELIVERY"
    if phase != "http_response":
        return "UNKNOWN_DELIVERY"
    if http_status is not None and 200 <= int(http_status) < 300:
        return "DELIVERED" if valid_provider_ack else "UNKNOWN_DELIVERY"
    if http_status in {408, 425, 429} or (http_status is not None and int(http_status) >= 500):
        return "RETRY"
    if http_status is not None and 400 <= int(http_status) < 500:
        return "FAILED_PERMANENT"
    return "UNKNOWN_DELIVERY"


def _safe_payload_snapshot(
    *,
    event: CanonicalCommercialEvent,
    projection: CanonicalMetaConversionProjection,
    dedup_decision: str,
    historical_no_send: bool,
) -> dict[str, Any]:
    # No raw user_data, CTWA id, phone, conversation text or credential enters
    # the SHADOW outbox. A later LIVE cutover must rebuild/revalidate payload
    # material from trusted tenant records and the currently resolved credential.
    return {
        "event_name": str(event.event_name),
        "event_id": str(projection.canonical_event_id),
        "event_time": projection.event_time,
        "action_source": projection.action_source,
        "payload_hash": projection.payload_hash,
        "dataset_id_masked": projection.dataset_id_masked,
        "waba_id_masked": projection.waba_id_masked,
        "ctwa_present": bool(projection.ctwa_present),
        "value": str(event.value) if event.revenue_verified and event.value is not None else None,
        "currency": event.currency if event.revenue_verified else None,
        "projection_status": projection.projection_status,
        "legacy_reference_classification": projection.legacy_reference_classification,
        "legacy_delivery_state": (projection.comparison_json or {}).get("legacy_delivery_state", "UNKNOWN_DELIVERY"),
        "dedup_decision": dedup_decision,
        "historical_no_send": bool(historical_no_send),
    }


async def ensure_shadow_outbox_intent(
    session: AsyncSession,
    *,
    event: CanonicalCommercialEvent,
    projection: CanonicalMetaConversionProjection,
) -> bool:
    """Insert one durable intent idempotently inside the projection transaction."""
    historical = bool(event.is_backfilled or event.external_dispatch_policy == "historical_no_send")
    mode = str(projection.mode or "disabled").lower()
    valid = projection.projection_status == "valid"
    legacy_delivery_state = str(
        (projection.comparison_json or {}).get("legacy_delivery_state") or "UNKNOWN_DELIVERY"
    )
    dedup = canonical_dedup_decision(
        historical_no_send=historical,
        projection_valid=valid,
        legacy_reference_classification=projection.legacy_reference_classification,
        legacy_delivery_state=legacy_delivery_state,
    )
    if historical:
        status = "HISTORICAL_BLOCKED"
    elif mode == "shadow" and valid and dedup in {"NO_LEGACY_REFERENCE", "SAFE_SHADOW_ONLY"}:
        # SHADOW denotes a non-sending audit intent, never a deliverable job.
        status = "SHADOW"
    else:
        status = "BLOCKED"
    dispatch_mode = "disabled" if mode == "disabled" else "shadow"
    statement = pg_insert(CanonicalConversionOutbox).values(
        id=uuid.uuid4(),
        empresa_id=event.empresa_id,
        commercial_event_id=event.id,
        projection_id=projection.id,
        provider="meta",
        destination_type="business_messaging_dataset",
        dataset_id_masked=projection.dataset_id_masked,
        waba_id_masked=projection.waba_id_masked,
        event_name=event.event_name,
        event_id=projection.canonical_event_id,
        idempotency_key=canonical_outbox_idempotency_key(projection.canonical_event_id),
        payload_snapshot=_safe_payload_snapshot(
            event=event,
            projection=projection,
            dedup_decision=dedup,
            historical_no_send=historical,
        ),
        payload_version=OUTBOX_PAYLOAD_VERSION,
        status=status,
        dispatch_mode=dispatch_mode,
        historical_no_send=historical,
        eligible_at=projection.last_projected_at,
    ).on_conflict_do_update(
        constraint="uq_canonical_conversion_outbox_event",
        set_={
            "projection_id": projection.id,
            "dataset_id_masked": projection.dataset_id_masked,
            "waba_id_masked": projection.waba_id_masked,
            "payload_snapshot": _safe_payload_snapshot(
                event=event,
                projection=projection,
                dedup_decision=dedup,
                historical_no_send=historical,
            ),
            "status": status,
            "dispatch_mode": dispatch_mode,
            "eligible_at": projection.last_projected_at,
            "updated_at": projection.last_projected_at,
        },
        where=and_(
            CanonicalConversionOutbox.historical_no_send.is_(False),
            CanonicalConversionOutbox.status.in_(["SHADOW", "BLOCKED"]),
        ),
    )
    result = await session.execute(statement)
    return bool(result.rowcount)


async def claim_shadow_outbox(
    session: AsyncSession,
    *,
    lease_owner: str,
    limit: int = 25,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Claim SHADOW-only rows with SKIP LOCKED and commit the lease first.

    Expired CLAIMED leases are reclaimable. No historical, blocked, or LIVE
    row is claimable. Commit-before-return is deliberate for future workers.
    """
    owner = str(lease_owner or "").strip()
    if not owner or len(owner) > 120 or any(ord(char) < 32 for char in owner):
        raise ValueError("outbox_lease_owner_invalid")
    batch_size = min(max(int(limit), 1), 100)
    lease_duration = min(max(int(lease_seconds), 10), 900)
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    candidates = (
        select(CanonicalConversionOutbox.id)
        .join(
            CanonicalMetaConversionProjection,
            and_(
                CanonicalMetaConversionProjection.empresa_id == CanonicalConversionOutbox.empresa_id,
                CanonicalMetaConversionProjection.id == CanonicalConversionOutbox.projection_id,
            ),
        )
        .where(
            CanonicalConversionOutbox.dispatch_mode == "shadow",
            CanonicalConversionOutbox.historical_no_send.is_(False),
            CanonicalMetaConversionProjection.legacy_reference_classification == "UNLINKED",
            CanonicalConversionOutbox.next_attempt_at.is_(None)
            | (CanonicalConversionOutbox.next_attempt_at <= current),
            or_(
                CanonicalConversionOutbox.status == "SHADOW",
                CanonicalConversionOutbox.status == "RETRY",
                and_(
                    CanonicalConversionOutbox.status == "CLAIMED",
                    CanonicalConversionOutbox.lease_expires_at <= current,
                ),
            ),
        )
        .order_by(CanonicalConversionOutbox.created_at.asc(), CanonicalConversionOutbox.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("claimable_conversion_outbox")
    )
    statement = (
        update(CanonicalConversionOutbox)
        .where(CanonicalConversionOutbox.id.in_(select(candidates.c.id)))
        .values(
            status="CLAIMED",
            lease_owner=owner,
            lease_expires_at=current + timedelta(seconds=lease_duration),
            attempt_count=CanonicalConversionOutbox.attempt_count + 1,
            last_attempt_at=current,
            updated_at=current,
        )
        .returning(
            CanonicalConversionOutbox.id,
            CanonicalConversionOutbox.empresa_id,
            CanonicalConversionOutbox.commercial_event_id,
            CanonicalConversionOutbox.event_id,
            CanonicalConversionOutbox.attempt_count,
            CanonicalConversionOutbox.lease_expires_at,
        )
    )
    rows = (await session.execute(statement)).all()
    await session.commit()
    return [
        {
            "outbox_id": str(row.id),
            "tenant_id": str(row.empresa_id),
            "commercial_event_id": str(row.commercial_event_id),
            "event_id": row.event_id,
            "attempt_number": int(row.attempt_count),
            "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
            "external_call_permitted": False,
        }
        for row in rows
    ]


async def record_shadow_simulation(
    session: AsyncSession,
    *,
    outbox_id: Any,
    lease_owner: str,
    simulated_error_class: str | None = None,
    simulated_retryable: bool = False,
    retry_delay_seconds: int = 15,
    now: datetime | None = None,
) -> None:
    """Append a non-delivery simulation, optionally scheduling a SHADOW retry."""
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    outbox = await session.scalar(select(CanonicalConversionOutbox).where(
        CanonicalConversionOutbox.id == uuid.UUID(str(outbox_id)),
        CanonicalConversionOutbox.dispatch_mode == "shadow",
        CanonicalConversionOutbox.historical_no_send.is_(False),
        CanonicalConversionOutbox.status == "CLAIMED",
        CanonicalConversionOutbox.lease_owner == str(lease_owner),
        CanonicalConversionOutbox.lease_expires_at > current,
    ).with_for_update())
    if outbox is None:
        raise ValueError("outbox_shadow_claim_not_owned")
    number = int(outbox.attempt_count or 0)
    retry_delay = min(max(int(retry_delay_seconds), 1), 3600)
    session.add(CanonicalConversionDeliveryAttempt(
        id=uuid.uuid4(),
        empresa_id=outbox.empresa_id,
        outbox_id=outbox.id,
        attempt_number=number,
        attempt_kind="SHADOW_SIMULATION",
        started_at=outbox.last_attempt_at or current,
        finished_at=current,
        status="SIMULATED",
        error_class=_safe_error_class(simulated_error_class),
        provider_ack_status="NOT_ATTEMPTED",
        request_correlation_id=f"shadow-{uuid.uuid4().hex}",
        response_metadata={
            "external_call": False,
            "provider_ack": "NOT_ATTEMPTED",
            "simulation_outcome": "RETRY" if simulated_retryable else "SHADOW",
        },
    ))
    outbox.status = "RETRY" if simulated_retryable else "SHADOW"
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    outbox.next_attempt_at = current + timedelta(seconds=retry_delay) if simulated_retryable else None
    outbox.last_error_class = _safe_error_class(simulated_error_class)
    outbox.last_error_code = (
        "shadow_retry_simulated" if simulated_retryable
        else "shadow_simulation_only" if simulated_error_class
        else None
    )
    outbox.updated_at = current
    await session.flush()


async def reconcile_missing_shadow_outbox(
    session: AsyncSession,
    *,
    empresa_id: Any,
    limit: int = 100,
) -> int:
    """Idempotently recover projections whose best-effort hook previously failed."""
    from app.services.meta.canonical_conversion_shadow import project_commercial_event

    tenant_id = uuid.UUID(str(empresa_id))
    from app.services.meta.legacy_delivery_reconciliation import reconcile_legacy_delivery_links
    try:
        async with session.begin_nested():
            await reconcile_legacy_delivery_links(session, empresa_id=tenant_id, limit=limit)
    except Exception as exc:
        _outbox_logger.warning("LEGACY_DELIVERY_LINK_RECONCILIATION_FAILED error_type=%s", _safe_error_class(type(exc).__name__))
    missing_ids = list((await session.execute(
        select(CanonicalCommercialEvent.id)
        .outerjoin(
            CanonicalMetaConversionProjection,
            and_(
                CanonicalMetaConversionProjection.empresa_id == CanonicalCommercialEvent.empresa_id,
                CanonicalMetaConversionProjection.commercial_event_id == CanonicalCommercialEvent.id,
            ),
        )
        .outerjoin(
            CanonicalConversionOutbox,
            and_(
                CanonicalConversionOutbox.empresa_id == CanonicalMetaConversionProjection.empresa_id,
                CanonicalConversionOutbox.projection_id == CanonicalMetaConversionProjection.id,
            ),
        )
        .where(
            CanonicalCommercialEvent.empresa_id == tenant_id,
            CanonicalConversionOutbox.id.is_(None),
        )
        .order_by(CanonicalCommercialEvent.created_at.asc())
        .limit(min(max(int(limit), 1), 500))
    )).scalars().all())
    recovered = 0
    for event_id in missing_ids:
        # Isolate each fact so one malformed/temporarily failing event cannot
        # roll back or starve the rest of the tenant's recovery batch.
        try:
            async with session.begin_nested():
                await project_commercial_event(
                    session, empresa_id=tenant_id, commercial_event_id=event_id, persist=True,
                )
            recovered += 1
        except Exception as exc:
            _outbox_logger.warning(
                "CONVERSION_OUTBOX_RECONCILIATION_EVENT_FAILED tenant_id=%s error_type=%s",
                str(tenant_id), _safe_error_class(type(exc).__name__),
            )
    await session.flush()
    return recovered


async def conversion_outbox_status(session: AsyncSession, *, empresa_id: Any) -> dict[str, Any]:
    """Read-only tenant-scoped counts, safe for the existing analytics status API."""
    tenant_id = uuid.UUID(str(empresa_id))
    outbox_rows = (await session.execute(select(
        CanonicalConversionOutbox.status,
        CanonicalConversionOutbox.payload_snapshot,
        CanonicalConversionOutbox.last_error_class,
        CanonicalConversionOutbox.last_error_code,
        CanonicalConversionOutbox.updated_at,
        CanonicalMetaConversionProjection.legacy_reference_classification,
        CanonicalMetaConversionProjection.delivery_state,
        CanonicalConversionOutbox.historical_no_send,
    ).join(
        CanonicalMetaConversionProjection,
        and_(
            CanonicalMetaConversionProjection.empresa_id == CanonicalConversionOutbox.empresa_id,
            CanonicalMetaConversionProjection.id == CanonicalConversionOutbox.projection_id,
        ),
    ).where(CanonicalConversionOutbox.empresa_id == tenant_id)
        .order_by(CanonicalConversionOutbox.updated_at.desc()))).all()
    counts: dict[str, int] = {}
    duplicate_risk = 0
    no_legacy_reference = 0
    possible_legacy_duplicate = 0
    unknown_legacy_delivery = 0
    exact_legacy_delivery = 0
    last_error = None
    for status, snapshot, error_class, error_code, _updated_at, legacy_class, delivery_state, _historical in outbox_rows:
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
        payload = snapshot if isinstance(snapshot, dict) else {}
        decision = _effective_dedup_decision(
            stored_decision=payload.get("dedup_decision"),
            legacy_reference_classification=legacy_class,
            legacy_delivery_state=delivery_state or payload.get("legacy_delivery_state"),
        )
        no_legacy_reference += int(decision == "NO_LEGACY_REFERENCE")
        possible_legacy_duplicate += int(decision == "POSSIBLE_LEGACY_DUPLICATE")
        unknown_legacy_delivery += int(decision == "UNKNOWN_LEGACY_DELIVERY")
        exact_legacy_delivery += int(decision == "EXACT_LEGACY_DELIVERY")
        if _is_duplicate_risk(
            stored_decision=decision,
            legacy_reference_classification=legacy_class,
            legacy_delivery_state=delivery_state or payload.get("legacy_delivery_state"),
        ):
            duplicate_risk += 1
        if last_error is None and (error_class or error_code):
            last_error = {"class": str(error_class)[:80] if error_class else None, "code": str(error_code)[:100] if error_code else None}

    legacy_rows = (await session.execute(select(
        CommercialMetaEventLink.classification, func.count()
    ).where(CommercialMetaEventLink.empresa_id == tenant_id).group_by(CommercialMetaEventLink.classification))).all()
    legacy_counts = {str(classification): int(count) for classification, count in legacy_rows}
    legacy_total = int(await session.scalar(select(func.count()).select_from(MetaConversionEvent).where(
        MetaConversionEvent.empresa_id == tenant_id,
    )) or 0)
    event_count = int(await session.scalar(select(func.count()).select_from(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
    )) or 0)
    projection_count = int(await session.scalar(select(func.count()).select_from(CanonicalMetaConversionProjection).where(
        CanonicalMetaConversionProjection.empresa_id == tenant_id,
    )) or 0)
    outbox_count = int(await session.scalar(select(func.count()).select_from(CanonicalConversionOutbox).where(
        CanonicalConversionOutbox.empresa_id == tenant_id,
    )) or 0)
    missing_outbox = max(0, event_count - outbox_count)
    last_processing = await session.scalar(select(func.max(CanonicalConversionDeliveryAttempt.finished_at)).where(
        CanonicalConversionDeliveryAttempt.empresa_id == tenant_id,
    ))
    return {
        "commercial_events": event_count,
        "shadow_projections": projection_count,
        "valid_projections": int(await session.scalar(select(func.count()).select_from(CanonicalMetaConversionProjection).where(
            CanonicalMetaConversionProjection.empresa_id == tenant_id,
            CanonicalMetaConversionProjection.projection_status == "valid",
        )) or 0),
        "invalid_projections": int(await session.scalar(select(func.count()).select_from(CanonicalMetaConversionProjection).where(
            CanonicalMetaConversionProjection.empresa_id == tenant_id,
            CanonicalMetaConversionProjection.projection_status.in_(["invalid", "unsupported", "live_blocked"]),
        )) or 0),
        "outbox_status_counts": counts,
        "outbox_shadow": counts.get("SHADOW", 0),
        "outbox_historical_blocked": counts.get("HISTORICAL_BLOCKED", 0),
        "outbox_live_ready": counts.get("READY", 0),
        "outbox_missing": missing_outbox,
        "outbox_duplicate_risk": duplicate_risk,
        "outbox_no_legacy_reference": no_legacy_reference,
        "outbox_possible_legacy_duplicate": possible_legacy_duplicate,
        "outbox_unknown_legacy_delivery": unknown_legacy_delivery,
        "outbox_exact_legacy_delivery": exact_legacy_delivery,
        "legacy_meta_events": legacy_total,
        "legacy_exact": legacy_counts.get("LINKED_EXACT", 0),
        "legacy_probable": legacy_counts.get("LINKED_PROBABLE", 0),
        "legacy_ambiguous": legacy_counts.get("AMBIGUOUS", 0),
        "legacy_unlinked": legacy_counts.get("UNLINKED", 0),
        "last_processing_at": last_processing.isoformat() if last_processing else None,
        "last_error": last_error,
        "live_enabled": CANONICAL_LIVE_ENABLED,
        "tenant_scoped": True,
    }
