"""Passive durable legacy transport telemetry and future SHADOW ownership validation.

No HTTP transport exists in this module. Telemetry errors never change legacy
send eligibility; timeouts/cancellation cannot be mistaken for confirmed failure.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from db.database import AsyncSessionLocal
from db.models import CanonicalCommercialEvent, ConversionDeliveryIdentity, LegacyConversionDeliveryEvidence, MetaConversionEvent, Conexao, TipoConexao, TrafficMetaConfig

SESSION_FACTORY = AsyncSessionLocal
TELEMETRY_TIMEOUT_SECONDS = 2.0
logger = logging.getLogger("uvicorn.error")
TRANSPORT_PATHS = (
    "ads_integration_service.enviar_payload_meta_business_messaging",
    "ads_integration_service.enviar_payload_meta_capi",
)
TELEMETRY_EVENT_NAMES = frozenset({"ViewContent", "LeadSubmitted", "QualifiedLead", "AddToCart", "InitiateCheckout", "OrderCreated", "CartAbandoned", "Purchase", "Refund", "OrderShipped", "OrderDelivered", "OrderCanceled", "OrderReturned", "RatingProvided", "ReviewProvided"})

# Website Lead is observed only; it does not expand canonical BM eligibility.
EVENT_NAMES = TELEMETRY_EVENT_NAMES
TELEMETRY_EVENT_NAMES = TELEMETRY_EVENT_NAMES | {"Lead"}


@dataclass(frozen=True)
class LegacyDeliveryContext:
    empresa_id: Any
    legacy_event_id: Any = None
    commercial_event_id: Any = None
    source_type: str = "unknown"
    source_id: Any = None
    canonical_source_id: Any = None


_CONTEXT: ContextVar[LegacyDeliveryContext | None] = ContextVar("legacy_conversion_delivery_context", default=None)


@contextmanager
def legacy_delivery_context(context: LegacyDeliveryContext):
    token = _CONTEXT.set(context)
    try:
        yield context
    finally:
        _CONTEXT.reset(token)


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def safe_external_event_id(value: Any) -> str | None:
    text = str(value or "")
    if re.fullmatch(r"[a-fA-F0-9]{64}", text):
        return text
    parsed = _uuid(text)
    return str(parsed) if parsed else None


def sanitized_provider_response(response: Any) -> dict:
    if not isinstance(response, dict):
        return {}
    result = {}
    count = response.get("events_received")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        result["events_received"] = count
    error = response.get("error")
    if isinstance(error, dict):
        result["has_error"] = True
        for name in ("code", "error_subcode"):
            value = error.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                result[name] = value
        if isinstance(error.get("is_transient"), bool):
            result["is_transient"] = error["is_transient"]
    trace = response.get("fbtrace_id")
    if not trace and isinstance(error, dict):
        trace = error.get("fbtrace_id")
    if trace:
        result["trace_hash"] = _hash(trace)
    return result


def delivery_outcome(*, attempted: bool, http_status: int | None, provider_response: Any, error_phase: str | None = None) -> tuple[str, bool]:
    if not attempted or error_phase == "before_request":
        return "NOT_SENT", False
    if error_phase is not None:
        return "DELIVERY_UNKNOWN", True
    safe = sanitized_provider_response(provider_response)
    if http_status is not None and http_status >= 400:
        return "PROVIDER_REJECTED", False
    if http_status is not None and 200 <= http_status < 300:
        if safe.get("events_received", 0) > 0 and not safe.get("has_error"):
            return "PROVIDER_ACCEPTED", False
        if safe.get("has_error"):
            return "PROVIDER_REJECTED", False
    return "DELIVERY_UNKNOWN", True


async def _bounded(operation, fallback):
    try:
        return await asyncio.wait_for(operation(), timeout=TELEMETRY_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("LEGACY_DELIVERY_EVIDENCE_INCOMPLETE error_type=%s", type(exc).__name__)
        return fallback


async def _validated_references(session, *, context, tenant, event_name, external_event_id):
    if context is None or _uuid(context.empresa_id) != tenant:
        return None, None, "UNLINKED"
    legacy_id = _uuid(context.legacy_event_id)
    commercial_id = _uuid(context.commercial_event_id)
    legacy = None
    if legacy_id:
        legacy = await session.scalar(select(MetaConversionEvent).where(
            MetaConversionEvent.empresa_id == tenant, MetaConversionEvent.id == legacy_id,
            MetaConversionEvent.event_name == event_name, MetaConversionEvent.event_id == str(external_event_id),
        ))
    if legacy is None:
        return None, None, "UNLINKED"
    if commercial_id:
        event = await session.scalar(select(CanonicalCommercialEvent).where(
            CanonicalCommercialEvent.empresa_id == tenant, CanonicalCommercialEvent.id == commercial_id,
            CanonicalCommercialEvent.event_name == event_name,
        ))
        if event is not None and event.lead_id is not None and event.lead_id == legacy.lead_id:
            # Context is not proof alone. Require an explicit shared source identity.
            refs = {str(event.id), str(event.tag_event_id or ""), str(event.source_id or ""), str(event.payment_id or ""), str(event.checkout_id or "")}
            legacy_refs = {str(legacy.trigger_source_event_id or ""), str(external_event_id)}
            if context.canonical_source_id is not None:
                legacy_refs.add(str(context.canonical_source_id))
            if refs.difference({""}).intersection(legacy_refs):
                return legacy.id, event.id, "EXACT_LINKED"
    return legacy.id, None, "UNLINKED"


async def start_delivery_attempt(*, empresa_id, dataset_id, waba_id, event_name, external_event_id, transport_path, request_correlation_id=None) -> uuid.UUID | None:
    context = _CONTEXT.get()
    async def operation():
        tenant = _uuid(empresa_id)
        if tenant is None or not dataset_id or not external_event_id or event_name not in TELEMETRY_EVENT_NAMES or transport_path not in TRANSPORT_PATHS:
            return None
        async with SESSION_FACTORY() as session:
            legacy_id, commercial_id, classification = await _validated_references(
                session, context=context, tenant=tenant, event_name=event_name, external_event_id=external_event_id,
            )
            same_tenant_context = context is not None and _uuid(context.empresa_id) == tenant
            source_type = context.source_type if same_tenant_context else "unknown"
            # Source labels are finite identifiers; arbitrary strings are not stored.
            source_type = source_type if re.fullmatch(r"[a-zA-Z0-9_.:-]{1,60}", str(source_type)) else "unknown"
            row = LegacyConversionDeliveryEvidence(
                id=uuid.uuid4(), empresa_id=tenant, commercial_event_id=commercial_id, legacy_event_id=legacy_id,
                external_event_id_hash=_hash(external_event_id), external_event_id_safe=safe_external_event_id(external_event_id),
                event_name=event_name, dataset_id_hash=_hash(dataset_id), waba_id_hash=_hash(waba_id) if waba_id else None,
                source_type=source_type, source_id_hash=_hash(context.source_id) if same_tenant_context and context.source_id else None,
                transport_path=transport_path, identity_classification=classification,
                request_correlation_id=_uuid(request_correlation_id) or uuid.uuid4(),
                attempt_started_at=datetime.now(timezone.utc), request_outcome="LOCAL_PREPARED", delivery_uncertainty=False,
                provider_response={},
            )
            session.add(row)
            await session.commit()
            return row.id
    return await _bounded(operation, None)


async def mark_delivery_attempted(attempt_id) -> bool:
    async def operation():
        async with SESSION_FACTORY() as session:
            row = await session.get(LegacyConversionDeliveryEvidence, _uuid(attempt_id), with_for_update=True)
            if row is None or row.attempt_finished_at is not None:
                return False
            row.request_outcome = "REQUEST_ATTEMPTED"
            row.delivery_uncertainty = True
            await session.commit()
            return True
    return await _bounded(operation, False)


async def finish_delivery_attempt(attempt_id, *, http_status=None, provider_response=None, error_phase=None) -> bool:
    async def operation():
        async with SESSION_FACTORY() as session:
            row = await session.get(LegacyConversionDeliveryEvidence, _uuid(attempt_id), with_for_update=True)
            if row is None or row.attempt_finished_at is not None:
                return False
            attempted = (row.request_outcome == "REQUEST_ATTEMPTED" or http_status is not None
                or error_phase not in {None, "before_request"})
            row.request_outcome, row.delivery_uncertainty = delivery_outcome(
                attempted=attempted, http_status=http_status, provider_response=provider_response, error_phase=error_phase,
            )
            row.http_status = http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
            row.provider_response = sanitized_provider_response(provider_response)
            row.attempt_finished_at = datetime.now(timezone.utc)
            await session.commit()
            return True
    return await _bounded(operation, False)


def shared_delivery_identity_key(*, empresa_id, business_fact_id, dataset_id, waba_id, event_name) -> str:
    parts = [str(_uuid(empresa_id)), str(_uuid(business_fact_id)), str(dataset_id), str(waba_id), str(event_name)]
    if _uuid(empresa_id) is None or _uuid(business_fact_id) is None or not dataset_id or not waba_id or event_name not in EVENT_NAMES:
        raise ValueError("incomplete shared identity")
    return _hash(json.dumps(parts, separators=(",", ":")))


async def _validate_bound_destination(session, *, tenant, dataset_id, waba_id):
    # Use current BM source of truth; masked projection IDs cannot establish ownership.
    from app.services.meta.waba_dataset import _connection_dataset_id, _connection_waba_id
    connections = (await session.execute(select(Conexao.id, Conexao.waba_id, Conexao.metadata_json).where(
        Conexao.empresa_id == tenant, cast(Conexao.tipo, String) == TipoConexao.META.name,
        Conexao.provider == "meta_official",
    ))).all()
    matched = [connection for connection in connections
        if _connection_dataset_id(connection) == str(dataset_id)
        and _connection_waba_id(connection) == str(waba_id)]
    if len(matched) != 1:
        raise ValueError("shared destination does not belong to tenant")
    config = (await session.execute(select(TrafficMetaConfig.dataset_id, TrafficMetaConfig.waba_id, TrafficMetaConfig.whatsapp_connection_id).where(TrafficMetaConfig.empresa_id == tenant))).first()
    if config is not None:
        if (config.dataset_id and str(config.dataset_id) != str(dataset_id)
            or config.waba_id and str(config.waba_id) != str(waba_id)
            or config.whatsapp_connection_id and config.whatsapp_connection_id != matched[0].id):
            raise ValueError("shared destination configuration conflicts")


async def claim_shared_delivery_identity(session, *, empresa_id, commercial_event_id, dataset_id, waba_id, event_name, external_event_id, owner, shadow_validation: bool = False) -> dict:
    if not shadow_validation or owner not in {"legacy", "canonical"}:
        raise ValueError("shared ownership is SHADOW validation only")
    tenant, fact = _uuid(empresa_id), _uuid(commercial_event_id)
    event = await session.scalar(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant, CanonicalCommercialEvent.id == fact,
        CanonicalCommercialEvent.event_name == event_name,
    ))
    if event is None:
        raise ValueError("commercial identity does not belong to tenant")
    await _validate_bound_destination(session, tenant=tenant, dataset_id=dataset_id, waba_id=waba_id)
    key = shared_delivery_identity_key(empresa_id=tenant, business_fact_id=fact, dataset_id=dataset_id, waba_id=waba_id, event_name=event_name)
    statement = insert(ConversionDeliveryIdentity).values(
        id=uuid.uuid4(), empresa_id=tenant, commercial_event_id=fact, identity_key=key,
        external_event_id_hash=_hash(external_event_id), owner=owner, ownership_mode="SHADOW_VALIDATION", delivery_state="RESERVED",
    ).on_conflict_do_nothing(index_elements=["empresa_id", "identity_key"]).returning(ConversionDeliveryIdentity.id)
    inserted = await session.scalar(statement)
    await session.commit()  # Atomic reservation durable before a hypothetical external operation.
    row = await session.scalar(select(ConversionDeliveryIdentity).where(
        ConversionDeliveryIdentity.empresa_id == tenant, ConversionDeliveryIdentity.identity_key == key,
    ))
    return {"claimed": inserted is not None, "owner": row.owner, "state": row.delivery_state,
            "identity_matches": row.external_event_id_hash == _hash(external_event_id), "external_call_permitted": False}


async def legacy_delivery_evidence_status(session, empresa_id) -> dict:
    tenant = _uuid(empresa_id)
    counts = dict((await session.execute(select(LegacyConversionDeliveryEvidence.request_outcome, func.count()).where(
        LegacyConversionDeliveryEvidence.empresa_id == tenant,
    ).group_by(LegacyConversionDeliveryEvidence.request_outcome))).all())
    last = await session.scalar(select(func.max(LegacyConversionDeliveryEvidence.attempt_started_at)).where(
        LegacyConversionDeliveryEvidence.empresa_id == tenant,
    ))
    return {"legacy_sender_paths_discovered": len(TRANSPORT_PATHS), "legacy_sender_paths_instrumented": len(TRANSPORT_PATHS),
        "legacy_sender_paths": list(TRANSPORT_PATHS), "legacy_delivery_attempts": sum(counts.values()),
        "legacy_provider_accepted": counts.get("PROVIDER_ACCEPTED", 0), "legacy_provider_rejected": counts.get("PROVIDER_REJECTED", 0),
        "legacy_unknown_delivery": counts.get("DELIVERY_UNKNOWN", 0) + counts.get("REQUEST_ATTEMPTED", 0),
        "legacy_delivery_outcomes": counts, "last_legacy_attempt_at": last.isoformat() if last else None,
        "shared_dedup_readiness": "SHADOW_VALIDATION_ONLY", "legacy_delivery_ownership_enforced": False}
