from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ads_integration_service import (
    _ctwa_clid,
    _custom_data,
    _dados_adicionais,
    atualizar_reserva_meta_capi,
    enviar_payload_meta_business_messaging,
    enviar_payload_meta_business_messaging_with_retry,
    payload_preview_seguro,
    registrar_evento_meta_capi,
    reservar_evento_meta_capi_live,
)
from app.services.meta.business_messaging_conversions import resolve_meta_conversion_credential
from app.services.meta.dataset_events import (
    BUSINESS_MESSAGING_WHATSAPP,
    MetaConversionPayloadValidationError,
    build_business_messaging_event,
    validate_business_messaging_event_payload,
)
from db.models import BusinessMessagingConversionMapping, CRMLead, Empresa


ALLOWED_TRIGGER_TYPES = {
    "tag_applied",
    "crm_stage_changed",
    "order_created",
    "payment_confirmed",
    "system_event",
}

ALLOWED_TRIGGER_SOURCES = {
    "manual",
    "ai",
    "funnel",
    "automation",
    "followup",
    "campaign",
    "integration",
    "system",
    "monitor",
}

TRIGGER_SOURCE_LABELS = {
    "manual": "Alteração manual",
    "ai": "Inteligência Artificial",
    "funnel": "Funil",
    "automation": "Automação",
    "followup": "Follow-up",
    "campaign": "Campanha",
    "integration": "Integração",
    "system": "Sistema",
    "monitor": "Monitor de Conversão",
}

DEFAULT_ALLOWED_TRIGGER_SOURCES = {"manual", "integration", "system"}

HIGH_QUALITY_EVENT_CONTRACT = {
    "LeadSubmitted": {"tag_applied", "crm_stage_changed", "system_event"},
    "QualifiedLead": {"tag_applied", "crm_stage_changed", "system_event"},
    "InitiateCheckout": {"tag_applied", "crm_stage_changed", "order_created", "system_event"},
    "OrderCreated": {"tag_applied", "order_created", "system_event"},
    "Purchase": {"tag_applied", "crm_stage_changed", "order_created", "payment_confirmed", "system_event"},
    "ViewContent": {"tag_applied", "system_event"},
    "AddToCart": {"tag_applied", "system_event"},
    "CartAbandoned": {"tag_applied", "system_event"},
}

EVENT_TO_COMMERCIAL_FACT = {
    "ViewContent": "content_engaged",
    "LeadSubmitted": "lead_established",
    "QualifiedLead": "qualification_confirmed",
    "AddToCart": "selection_confirmed",
    "InitiateCheckout": "checkout_intent",
    "OrderCreated": "order_commitment",
    "CartAbandoned": "abandonment",
    "Purchase": "purchase_confirmed",
}

VALID_SEND_MODES = {"disabled", "dry_run", "test_event", "live"}


class BusinessMessagingConversionError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessMessagingTrigger:
    empresa_id: Any
    lead_id: Any
    trigger_type: str
    trigger_ref: str
    source_event_id: str
    trigger_source: str = "system"
    send_real: bool = False
    event_time: int | None = None
    payload: dict[str, Any] | None = None
    fact_id: str | None = None
    canonical_commercial_event_id: Any = None


@dataclass(frozen=True)
class ConversionDomainEvent:
    empresa_id: Any
    event_type: str
    lead_id: Any
    source_event_id: str
    event_time: int
    trigger_ref: str | None = None
    trigger_source: str = "system"
    payload: dict[str, Any] | None = None
    send_real: bool = False
    fact_id: str | None = None
    canonical_commercial_event_id: Any = None


def stable_business_messaging_event_id(*, empresa_id: Any, lead_id: Any, trigger_type: str, trigger_ref: str, source_event_id: str, event_name: str) -> str:
    raw = f"{empresa_id}:{lead_id}:{trigger_type}:{trigger_ref}:{source_event_id}:{event_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_business_fact_id(*, empresa_id: Any, lead_id: Any, fact_type: str, anchor: str) -> str:
    raw = f"{empresa_id}:{lead_id}:commercial_fact:{str(fact_type).strip().lower()}:{str(anchor).strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_event_trigger_contract(event_name: str, trigger_type: str) -> None:
    allowed = HIGH_QUALITY_EVENT_CONTRACT.get(str(event_name or "").strip())
    if not allowed or str(trigger_type or "").strip() not in allowed:
        raise BusinessMessagingConversionError("event_trigger_contract_invalid")


def normalize_trigger_source(value: Any) -> str:
    source = str(value or "").strip().lower()
    if source not in ALLOWED_TRIGGER_SOURCES:
        raise BusinessMessagingConversionError("trigger_source_invalid")
    return source


def mapping_allowed_sources(mapping: BusinessMessagingConversionMapping) -> set[str]:
    metadata = getattr(mapping, "metadata_json", None) or {}
    raw = metadata.get("allowed_sources") if isinstance(metadata, dict) else None
    if not isinstance(raw, list):
        return set(DEFAULT_ALLOWED_TRIGGER_SOURCES)
    normalized = {str(item or "").strip().lower() for item in raw}
    valid = normalized.intersection(ALLOWED_TRIGGER_SOURCES)
    return valid or set(DEFAULT_ALLOWED_TRIGGER_SOURCES)


def mapping_test_event_code(mapping: BusinessMessagingConversionMapping) -> str | None:
    metadata = getattr(mapping, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return None
    value = str(metadata.get("test_event_code") or "").strip()
    return value or None


def source_options() -> list[dict[str, str]]:
    return [{"value": key, "label": TRIGGER_SOURCE_LABELS[key]} for key in sorted(TRIGGER_SOURCE_LABELS)]


def _safe_domain_custom_data(domain_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = domain_payload if isinstance(domain_payload, dict) else {}
    custom: dict[str, Any] = {}
    amount = payload.get("amount")
    if amount is None:
        amount = payload.get("value")
    if amount is not None:
        try:
            custom["value"] = float(amount)
        except (TypeError, ValueError):
            pass
    currency = str(payload.get("currency") or "").strip().upper()
    if currency and len(currency) == 3:
        custom["currency"] = currency
    for key in ("order_id", "content_ids", "contents"):
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "order_id":
            custom[key] = str(value).strip()[:100]
        elif isinstance(value, list):
            custom[key] = value[:100]
    return custom


def _business_messaging_custom_data(
    lead: CRMLead,
    mapping: BusinessMessagingConversionMapping,
    domain_payload: dict[str, Any] | None,
    event_name: str,
) -> dict[str, Any]:
    dados = _dados_adicionais(lead)
    custom = _custom_data(lead, mapping, dados)
    domain_custom = _safe_domain_custom_data(domain_payload)
    if "value" in domain_custom:
        custom["value"] = domain_custom["value"]
    if "currency" in domain_custom:
        custom["currency"] = domain_custom["currency"]
    # Mapping currency is a configured source of truth; there is no hardcoded fallback here.
    if event_name == "OrderCreated":
        mapping_currency = str(getattr(mapping, "currency", "") or "").strip().upper()
        if "currency" not in domain_custom and len(mapping_currency) == 3:
            custom["currency"] = mapping_currency
        elif "currency" not in domain_custom and len(mapping_currency) != 3:
            # _custom_data has a legacy default for older event builders; it is not
            # an acceptable source for OrderCreated and must not become a fake BRL.
            custom.pop("currency", None)
    for key in ("order_id", "content_ids", "contents"):
        if key in domain_custom:
            custom[key] = domain_custom[key]
    return custom


async def _exact_canonical_source(session, trigger, event_name):
    """Explicit producer identity only: a shared lead or timestamp is insufficient."""
    if trigger.canonical_commercial_event_id is None:
        return None, None
    import logging
    import uuid
    from db.models import CanonicalCommercialEvent, CRMTagEvent
    try:
        async with session.begin_nested():
            event = await session.scalar(select(CanonicalCommercialEvent).where(
                CanonicalCommercialEvent.empresa_id == trigger.empresa_id,
                CanonicalCommercialEvent.id == uuid.UUID(str(trigger.canonical_commercial_event_id)),
                CanonicalCommercialEvent.lead_id == trigger.lead_id,
                CanonicalCommercialEvent.event_name == event_name,
            ))
            if event is None:
                return None, None
            if trigger.trigger_type == "tag_applied" and event.tag_event_id is not None:
                tag_event = await session.scalar(select(CRMTagEvent).where(
                    CRMTagEvent.empresa_id == trigger.empresa_id, CRMTagEvent.id == event.tag_event_id,
                    CRMTagEvent.lead_id == trigger.lead_id,
                    CRMTagEvent.tag_id == uuid.UUID(str(trigger.trigger_ref)), CRMTagEvent.action == "applied",
                ))
                if tag_event is not None:
                    return event.id, tag_event.id
            if str(trigger.source_event_id) in {str(event.source_id), str(event.payment_id or ""), str(event.checkout_id or "")}:
                return event.id, trigger.source_event_id
    except Exception as exc:
        logging.getLogger("uvicorn.error").warning("LEGACY_DELIVERY_IDENTITY_INCOMPLETE error_type=%s", type(exc).__name__)
    return None, None


async def emit_conversion_domain_event(
    session: AsyncSession,
    event: ConversionDomainEvent,
) -> dict[str, Any]:
    event_type = str(event.event_type or "").strip()
    trigger_ref = str(event.trigger_ref or event_type).strip()
    source_event_id = str(event.source_event_id or "").strip()
    if not event_type or not trigger_ref or not source_event_id:
        raise BusinessMessagingConversionError("domain_event_invalid")
    return await process_business_messaging_conversion_trigger(
        session,
        BusinessMessagingTrigger(
            empresa_id=event.empresa_id,
            lead_id=event.lead_id,
            trigger_type="system_event" if event_type in {"ctwa_lead_created", "checkout_created"} else event_type,
            trigger_ref=trigger_ref,
            source_event_id=source_event_id,
            trigger_source=event.trigger_source,
            send_real=event.send_real,
            event_time=event.event_time,
            payload=event.payload or {},
            fact_id=event.fact_id,
            canonical_commercial_event_id=event.canonical_commercial_event_id,
        ),
    )


async def get_active_mapping(
    session: AsyncSession,
    *,
    empresa_id: Any,
    trigger_type: str,
    trigger_ref: str,
) -> BusinessMessagingConversionMapping | None:
    trigger_type = str(trigger_type or "").strip()
    trigger_ref = str(trigger_ref or "").strip()
    if trigger_type not in ALLOWED_TRIGGER_TYPES or not trigger_ref:
        raise BusinessMessagingConversionError("conversion_trigger_invalid")
    return (
        await session.execute(
            select(BusinessMessagingConversionMapping).where(
                BusinessMessagingConversionMapping.empresa_id == empresa_id,
                BusinessMessagingConversionMapping.trigger_type == trigger_type,
                BusinessMessagingConversionMapping.trigger_ref == trigger_ref,
                BusinessMessagingConversionMapping.ativo.is_(True),
            )
        )
    ).scalars().first()


async def process_business_messaging_conversion_trigger(
    session: AsyncSession,
    trigger: BusinessMessagingTrigger,
) -> dict[str, Any]:
    trigger_type = str(trigger.trigger_type or "").strip()
    trigger_ref = str(trigger.trigger_ref or "").strip()
    source_event_id = str(trigger.source_event_id or "").strip()
    trigger_source = normalize_trigger_source(trigger.trigger_source)
    if trigger_type not in ALLOWED_TRIGGER_TYPES or not trigger_ref or not source_event_id:
        raise BusinessMessagingConversionError("conversion_trigger_invalid")

    empresa = (await session.execute(select(Empresa).where(Empresa.id == trigger.empresa_id))).scalars().first()
    lead = (await session.execute(select(CRMLead).where(CRMLead.id == trigger.lead_id, CRMLead.empresa_id == trigger.empresa_id))).scalars().first()
    if not empresa or not lead:
        raise BusinessMessagingConversionError("tenant_lead_not_found")

    mapping = await get_active_mapping(session, empresa_id=trigger.empresa_id, trigger_type=trigger_type, trigger_ref=trigger_ref)
    if not mapping:
        return {"status": "skipped", "reason": "mapping_not_found"}

    event_name = str(mapping.event_name or "").strip()
    validate_event_trigger_contract(event_name, trigger_type)
    event_identity = str(trigger.fact_id or "").strip()
    if not event_identity:
        fact_type = EVENT_TO_COMMERCIAL_FACT.get(event_name)
        if trigger_type == "tag_applied" and fact_type:
            # A tag is the canonical operational representation.  The actor
            # and source reference must not alter the identity on retries.
            event_identity = stable_business_fact_id(
                empresa_id=trigger.empresa_id, lead_id=trigger.lead_id,
                fact_type=fact_type, anchor=f"tag:{trigger_ref}",
            )
        else:
            event_identity = (
                stable_business_fact_id(
                    empresa_id=trigger.empresa_id, lead_id=trigger.lead_id,
                    fact_type=fact_type, anchor=source_event_id,
                ) if fact_type and source_event_id else source_event_id
            )
    allowed_sources = mapping_allowed_sources(mapping)
    if trigger_source not in allowed_sources:
        event_id = stable_business_messaging_event_id(
            empresa_id=trigger.empresa_id,
            lead_id=trigger.lead_id,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            source_event_id=event_identity,
            event_name=event_name,
        )
        await registrar_evento_meta_capi(
            session,
            empresa=empresa,
            lead=lead,
            tag=None,
            config=None,
            event_name=event_name,
            event_id=event_id,
            status="blocked",
            error_message="trigger_source_not_allowed",
            source=trigger_source,
            conversion_context=BUSINESS_MESSAGING_WHATSAPP,
            ctwa_present=bool(_ctwa_clid(_dados_adicionais(lead))),
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            trigger_source=trigger_source,
            trigger_source_event_id=source_event_id,
        )
        return {"status": "blocked", "reason": "trigger_source_not_allowed", "event_id": event_id}
    mode = str(mapping.send_mode or "dry_run").strip().lower()
    if mode not in VALID_SEND_MODES:
        mode = "dry_run"
    if mode == "disabled":
        return {"status": "skipped", "reason": "send_mode_disabled"}
    test_event_code = mapping_test_event_code(mapping) if mode == "test_event" else None

    ctwa_clid = _ctwa_clid(_dados_adicionais(lead))
    if not ctwa_clid:
        event_id = stable_business_messaging_event_id(
            empresa_id=trigger.empresa_id,
            lead_id=trigger.lead_id,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            source_event_id=event_identity,
            event_name=event_name,
        )
        await registrar_evento_meta_capi(
            session,
            empresa=empresa,
            lead=lead,
            tag=None,
            config=None,
            event_name=event_name,
            event_id=event_id,
            status="not_applicable",
            payload={
                "commercial_event_status": "confirmed",
                "meta_delivery_status": "not_applicable",
                "meta_delivery_reason": "no_ctwa_attribution",
            },
            error_message="no_ctwa_attribution",
            source=trigger_source,
            conversion_context=BUSINESS_MESSAGING_WHATSAPP,
            ctwa_present=False,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            trigger_source=trigger_source,
            trigger_source_event_id=source_event_id,
        )
        return {
            "status": "not_applicable",
            "reason": "no_ctwa_attribution",
            "event_id": event_id,
            "commercial_event_status": "confirmed",
            "meta_delivery_status": "not_applicable",
        }

    credential = await resolve_meta_conversion_credential(session, trigger.empresa_id)
    event_id = stable_business_messaging_event_id(
        empresa_id=trigger.empresa_id,
        lead_id=trigger.lead_id,
        trigger_type=trigger_type,
        trigger_ref=trigger_ref,
        source_event_id=event_identity,
        event_name=event_name,
    )
    payload = build_business_messaging_event(
        event_name=event_name,
        event_id=event_id,
        credential=credential,
        ctwa_clid=ctwa_clid,
        custom_data=_business_messaging_custom_data(lead, mapping, trigger.payload, event_name),
        event_time=trigger.event_time or int(time.time()),
    )
    if test_event_code:
        payload["test_event_code"] = test_event_code
    reservation_key = f"business_messaging:{event_id}"

    try:
        validate_business_messaging_event_payload(payload)
    except MetaConversionPayloadValidationError as error:
        await registrar_evento_meta_capi(
            session,
            empresa=empresa,
            lead=lead,
            tag=None,
            config=mapping,
            event_name=event_name,
            event_id=event_id,
            reservation_key=reservation_key,
            status="blocked",
            payload=payload,
            response_preview={
                "preflight": "blocked",
                "reason_code": error.reason_code,
                "message": str(error),
            },
            error_message=error.reason_code,
            source=trigger_source,
            conversion_context=BUSINESS_MESSAGING_WHATSAPP,
            credential=credential,
            ctwa_present=True,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            trigger_source=trigger_source,
            trigger_source_event_id=source_event_id,
            attempts=0,
        )
        return {"status": "blocked", "reason": error.reason_code, "event_id": event_id}

    if mode in {"dry_run", "test_event"} and not trigger.send_real:
        await registrar_evento_meta_capi(
            session,
            empresa=empresa,
            lead=lead,
            tag=None,
            config=None,
            event_name=event_name,
            event_id=event_id,
            reservation_key=reservation_key,
            status="dry_run",
            payload=payload,
            source=trigger_source,
            conversion_context=BUSINESS_MESSAGING_WHATSAPP,
            credential=credential,
            ctwa_present=True,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            trigger_source=trigger_source,
            trigger_source_event_id=source_event_id,
            test_event_code=test_event_code,
        )
        return {"status": "dry_run", "event_id": event_id, "payload_preview": payload_preview_seguro(payload)}

    if mode == "live" and not bool(getattr(empresa, "meta_capi_ativo", False)):
        return {"status": "skipped", "reason": "meta_capi_ativo_false", "event_id": event_id}

    try:
        reserved, duplicate_reason = await reservar_evento_meta_capi_live(
            session,
            empresa=empresa,
            lead=lead,
            tag=None,
            config=mapping,
            event_name=event_name,
            event_id=event_id,
            reservation_key=reservation_key,
            payload=payload,
            source=trigger_source,
            trigger_source=trigger_source,
        )
    except IntegrityError:
        return {"status": "skipped", "reason": "duplicate_event_reserved", "event_id": event_id}
    if reserved is None:
        return {"status": "skipped", "reason": duplicate_reason, "event_id": event_id}
    reserved.conversion_context = BUSINESS_MESSAGING_WHATSAPP
    reserved.action_source = "business_messaging"
    reserved.messaging_channel = "whatsapp"
    reserved.credential_source = credential.credential_source
    reserved.waba_id_masked = _mask_id(credential.waba_id)
    reserved.dataset_id_masked = _mask_id(credential.dataset_id)
    reserved.ctwa_present = True
    reserved.trigger_type = trigger_type
    reserved.trigger_ref = trigger_ref
    reserved.trigger_source = trigger_source
    reserved.trigger_source_event_id = source_event_id
    reserved.test_event_code = test_event_code
    # Passive evidence records the committed reservation without gating legacy delivery.
    from app.services.meta.legacy_delivery_evidence import LegacyDeliveryContext, legacy_delivery_context
    canonical_id, canonical_source_id = await _exact_canonical_source(session, trigger, event_name)
    with legacy_delivery_context(LegacyDeliveryContext(
        empresa_id=empresa.id, legacy_event_id=reserved.id,
        commercial_event_id=canonical_id, canonical_source_id=canonical_source_id,
        source_type=trigger_type, source_id=source_event_id,
    )):
        status_code, body, attempts, reason_code, attempt_preview = await enviar_payload_meta_business_messaging_with_retry(
            credential, payload, event_id=event_id, event_name=event_name,
        )
    final_status = "sent" if status_code < 400 else "failed"
    await atualizar_reserva_meta_capi(
        session,
        reserved,
        status=final_status,
        response_preview=attempt_preview or {"status_code": status_code, "body": body},
        error_message=None if final_status == "sent" else reason_code,
        attempts=attempts,
    )
    return {"status": final_status, "event_id": event_id, "attempts": attempts, "response_preview": attempt_preview or {"status_code": status_code, "body": body}}


def _mask_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None
