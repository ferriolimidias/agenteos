"""Canonical Business Messaging conversion projections, with no external sender.

This module deliberately does not import a Graph client or HTTP transport. It
can build and persist an internal projection, but its dispatcher is a hard
block until a separately approved P1C cutover.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.business_messaging_conversion_engine import (
    HIGH_QUALITY_EVENT_CONTRACT,
    _business_messaging_custom_data,
    mapping_allowed_sources,
    normalize_trigger_source,
)
from app.services.meta.business_messaging_conversions import resolve_meta_conversion_credential
from app.services.meta.conversion_credentials import CONVERSIONS_PURPOSE, MetaCredentialResolutionError
from app.services.meta.dataset_events import (
    BUSINESS_MESSAGING_EVENTS,
    MetaConversionPayloadValidationError,
    build_business_messaging_event,
    validate_business_messaging_event_payload,
)
from db.models import (
    AttributionTouch,
    BusinessMessagingConversionMapping,
    CanonicalCommercialEvent,
    CanonicalMetaConversionProjection,
    CommercialMetaEventLink,
    CommercialMetaEventPolicy,
    CRMLead,
    CRMTagEvent,
    MetaConversionEvent,
)

logger = logging.getLogger(__name__)

SUPPORTED_CANONICAL_EVENTS = frozenset({
    "ViewContent", "LeadSubmitted", "QualifiedLead", "AddToCart",
    "InitiateCheckout", "OrderCreated", "CartAbandoned", "Purchase",
})
PROJECTION_VERSION = 1
_COMPARISON_CLASSES = {
    "EXACT", "SEMANTICALLY_EQUIVALENT", "MISSING_ATTRIBUTION",
    "MISSING_REQUIRED_FIELDS", "EVENT_ID_MISMATCH", "VALUE_MISMATCH",
    "UNSUPPORTED_EVENT", "NO_LEGACY_REFERENCE", "EVENT_NAME_MISMATCH",
    "CONTEXT_MISMATCH",
}

_CREDENTIAL_ERROR_FIELDS = {
    "BLOCKED_DATASET_NOT_LINKED": "dataset_id",
    "BLOCKED_WABA_NOT_LINKED": "waba_id",
    "CONVERSION_CREDENTIAL_NOT_CONFIGURED": "business_messaging_credential",
    "CREDENTIAL_NOT_CONFIGURED": "business_messaging_credential",
    "CONVERSION_TOKEN_DECRYPT_FAILED": "business_messaging_credential",
    "ASSET_ACCESS_DENIED": "business_messaging_credential",
    "OFFICIAL_WHATSAPP_CONNECTION_MISSING": "business_messaging_credential",
}


def _reason_categories(missing_fields: list[str], *, unsupported: bool = False) -> list[str]:
    fields = {str(value or "").strip().lower() for value in missing_fields if str(value or "").strip()}
    categories: set[str] = set()
    if unsupported:
        categories.add("UNSUPPORTED_CONTRACT")
    for field in fields:
        if "dataset" in field:
            categories.add("MISSING_DATASET")
        elif "waba" in field or "business_identity" in field or field in {"tenant_business", "business_id"}:
            categories.add("MISSING_BUSINESS_IDENTITY")
        elif "credential" in field or "token" in field:
            categories.add("MISSING_CREDENTIAL")
        elif "ctwa" in field or "attribution" in field:
            categories.add("MISSING_CTWA_CLID")
        elif "event_time" in field or "occurred_at" in field:
            categories.add("MISSING_EVENT_TIME")
        elif "event_id" in field or "canonical_event_id" in field:
            categories.add("MISSING_EVENT_ID")
        elif "event_name" in field:
            categories.add("MISSING_EVENT_NAME")
        elif "currency" in field or field in {"value", "payment_value"}:
            categories.add("MISSING_VALUE_CURRENCY")
        elif "mapping" in field or "trigger_source" in field:
            categories.add("INVALID_MAPPING")
        elif "verified_payment" in field or "payment_id" in field or "payment_approved" in field:
            categories.add("MISSING_BUSINESS_EVIDENCE")
        else:
            categories.add("OTHER")
    return sorted(categories)


def _eligibility_classification(
    *,
    projection_status: str,
    missing_fields: list[str],
    unsupported: bool,
) -> str:
    """Classify local payload readiness without implying external delivery."""
    if unsupported or projection_status == "unsupported":
        return "INELIGIBLE_UNSUPPORTED_EVENT"
    fields = {str(value or "").strip().lower() for value in missing_fields}
    if projection_status == "valid":
        return "VALID_PAYLOAD_SHADOW"
    if projection_status == "disabled":
        return "INELIGIBLE_POLICY_DISABLED"
    if projection_status == "live_blocked":
        return "CONFIGURATION_BLOCKED"
    if fields.intersection({"verified_payment_evidence", "payment_id", "payment_approved"}):
        return "INELIGIBLE_NO_BUSINESS_EVIDENCE"
    if any("dataset" in field or "waba" in field or "credential" in field or "token" in field for field in fields):
        return "CONFIGURATION_BLOCKED"
    if any("ctwa" in field or "attribution" in field for field in fields):
        return "INELIGIBLE_NO_ATTRIBUTION"
    return "INVALID_MISSING_FIELDS"


def canonical_conversion_event_id(event: CanonicalCommercialEvent) -> str:
    """Stable across retries and source paths for the same tenant business fact."""
    tenant = str(event.empresa_id)
    identity = str(event.idempotency_key or event.id)
    return hashlib.sha256(f"agentos:business-messaging:v1:{tenant}:{identity}".encode()).hexdigest()


def canonical_delivery_event_id(
    event: CanonicalCommercialEvent,
    *,
    canonical_event_id: str,
    legacy_reference_classification: str,
    legacy_event: MetaConversionEvent | None,
) -> tuple[str, str]:
    """Reuse the provider id only when the existing CRM delivery is exactly linked.

    This makes a future adapter cutover idempotent with the current JEV/tag
    executor. Probable or ambiguous matches never donate an event id.
    """
    if (
        str(legacy_reference_classification).upper() == "EXACT_LINKED"
        and legacy_event is not None
        and str(legacy_event.empresa_id) == str(event.empresa_id)
        and str(legacy_event.event_name) == str(event.event_name)
        and str(legacy_event.lead_id or "") == str(event.lead_id or "")
    ):
        value = str(legacy_event.event_id or "").strip()
        if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
            return value, "exact_legacy_delivery"
    return str(canonical_event_id), "canonical_commercial_event"


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _masked_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


def _safe_payload_mask(value: Any) -> str | None:
    """Match the legacy sanitized preview without persisting raw identity data."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) <= 8:
        return f"{raw[:2]}***{raw[-2:]}"
    return f"{raw[:4]}...{raw[-4:]}"


def _normalized_payload_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
        return amount.normalize() if amount.is_finite() else None
    except Exception:
        return None


def _ctwa_clid(lead: CRMLead | None) -> str | None:
    data = getattr(lead, "dados_adicionais", None)
    attribution = data.get("meta_attribution") if isinstance(data, dict) else None
    last_touch = attribution.get("last_touch") if isinstance(attribution, dict) else None
    value = last_touch.get("ctwa_clid") if isinstance(last_touch, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


class ExternalConversionDispatcher:
    """P1C.1 dispatcher boundary. Every invocation is non-sending by design."""

    async def dispatch(
        self,
        *,
        mode: str,
        historical_no_send: bool = False,
        legacy_reference_classification: str = "UNLINKED",
        legacy_delivery_state: str = "UNKNOWN_DELIVERY",
    ) -> dict[str, Any]:
        normalized = str(mode or "disabled").strip().lower()
        if historical_no_send:
            return {"attempted": False, "state": "META_SIGNAL_NOT_SENT", "reason": "historical_no_send"}
        if (
            str(legacy_reference_classification) == "EXACT_LINKED"
            and str(legacy_delivery_state) in {"ALREADY_DELIVERED_BY_LEGACY", "LEGACY_ACCEPTED"}
        ):
            return {"attempted": False, "state": "META_SIGNAL_SENT", "reason": "exact_legacy_delivery_proof"}
        if normalized == "shadow":
            return {"attempted": False, "state": "META_SIGNAL_NOT_SENT", "reason": "shadow_only"}
        if normalized == "live":
            return {"attempted": False, "state": "META_SIGNAL_BLOCKED", "reason": "p1c1_live_dispatch_disabled"}
        return {"attempted": False, "state": "META_SIGNAL_NOT_SENT", "reason": "dispatch_disabled"}


def _legacy_delivery_state(legacy: MetaConversionEvent | None) -> str:
    if legacy is None:
        return "UNKNOWN_DELIVERY"
    status = str(legacy.status or "").strip().lower()
    if status == "accepted":
        return "LEGACY_ACCEPTED"
    if status == "sent":
        response = legacy.response_preview if isinstance(legacy.response_preview, dict) else {}
        body = response.get("body") if isinstance(response.get("body"), dict) else response
        received = body.get("events_received") if isinstance(body, dict) else None
        # Endpoint acceptance is not proof of downstream attribution.
        return "LEGACY_ACCEPTED" if isinstance(received, (int, float)) and received > 0 else "UNKNOWN_DELIVERY"
    if status == "failed":
        return "LEGACY_FAILED"
    if status == "not_applicable":
        return "NOT_APPLICABLE"
    return "UNKNOWN_DELIVERY"


async def _legacy_reference(
    session: AsyncSession,
    event: CanonicalCommercialEvent,
) -> tuple[str, MetaConversionEvent | None, int]:
    exact_link = await session.scalar(
        select(CommercialMetaEventLink).where(
            CommercialMetaEventLink.empresa_id == event.empresa_id,
            CommercialMetaEventLink.commercial_event_id == event.id,
            CommercialMetaEventLink.classification == "LINKED_EXACT",
        ).order_by(CommercialMetaEventLink.reconciled_at.desc())
    )
    if exact_link is not None:
        exact_event = await session.scalar(select(MetaConversionEvent).where(
            MetaConversionEvent.empresa_id == event.empresa_id,
            MetaConversionEvent.id == exact_link.meta_event_id,
        ))
        return "EXACT_LINKED", exact_event, 1

    legacy_rows = list((await session.execute(select(MetaConversionEvent).where(
        MetaConversionEvent.empresa_id == event.empresa_id,
        MetaConversionEvent.event_name == event.event_name,
        MetaConversionEvent.lead_id == event.lead_id if event.lead_id else MetaConversionEvent.lead_id.is_(None),
    ))).scalars().all())
    canonical_refs = {
        str(event.tag_event_id or ""),
        str((event.evidence_json or {}).get("tag_event_id") or ""),
        str(event.source_id or ""),
    } - {""}
    exact_candidates = [row for row in legacy_rows if canonical_refs.intersection({
        str(row.trigger_source_event_id or ""), str(row.event_id or ""),
    } - {""})]
    if len(exact_candidates) == 1:
        # Only return exact evidence; no probable candidate is ever persisted as an FK.
        return "EXACT_LINKED", exact_candidates[0], 1
    if len(exact_candidates) > 1:
        return "AMBIGUOUS", None, len(exact_candidates)

    canonical_time = event.occurred_at
    if canonical_time.tzinfo is None:
        canonical_time = canonical_time.replace(tzinfo=timezone.utc)
    probable: list[MetaConversionEvent] = []
    for row in legacy_rows:
        legacy_time = row.criado_em or row.last_attempt_at
        if legacy_time is None:
            continue
        if legacy_time.tzinfo is None:
            legacy_time = legacy_time.replace(tzinfo=timezone.utc)
        if abs((canonical_time - legacy_time).total_seconds()) <= 600:
            probable.append(row)
    if len(probable) == 1:
        return "PROBABLE_ONLY", None, 1
    if len(probable) > 1:
        return "AMBIGUOUS", None, len(probable)
    return "UNLINKED", None, 0


async def _resolve_mapping(
    session: AsyncSession,
    event: CanonicalCommercialEvent,
) -> tuple[BusinessMessagingConversionMapping | None, str | None, str | None, str | None]:
    """Resolve only exact producer contracts; never guess among mappings."""
    tenant_id = event.empresa_id
    trigger_type: str | None = None
    trigger_ref: str | None = None
    source = "system"

    if event.tag_event_id:
        tag_event = await session.scalar(select(CRMTagEvent).where(
            CRMTagEvent.empresa_id == tenant_id,
            CRMTagEvent.id == event.tag_event_id,
            CRMTagEvent.action == "applied",
        ))
        if tag_event is None:
            return None, None, None, "canonical_tag_evidence_missing"
        trigger_type, trigger_ref = "tag_applied", str(tag_event.tag_id)
        try:
            source = normalize_trigger_source(tag_event.source or "system")
        except ValueError:
            return None, trigger_type, trigger_ref, "trigger_source_invalid"
    elif event.funnel_stage_id:
        trigger_type, trigger_ref = "crm_stage_changed", str(event.funnel_stage_id)
        source = "funnel"
    elif event.source_type == "attribution_touch":
        touch = None
        try:
            touch_id = uuid.UUID(str(event.source_id)) if event.source_id else None
        except (TypeError, ValueError):
            touch_id = None
        if touch_id:
            touch = await session.scalar(select(AttributionTouch).where(
                AttributionTouch.empresa_id == tenant_id,
                AttributionTouch.id == touch_id,
            ))
        event_kind = str((event.evidence_json or {}).get("event_kind") or getattr(touch, "event_kind", "") or "")
        if event.event_name == "InitiateCheckout" and event_kind == "checkout_started":
            trigger_type, trigger_ref, source = "system_event", "checkout_created", "system"
        elif event.event_name == "OrderCreated" and event_kind == "order_created":
            trigger_type, trigger_ref, source = "order_created", "order_created", "system"
        elif event.event_name == "Purchase" and event_kind == "payment_approved":
            trigger_type, trigger_ref, source = "payment_confirmed", "mercado_pago_approved", "integration"

    if trigger_type and trigger_ref:
        mapping = await session.scalar(select(BusinessMessagingConversionMapping).where(
            BusinessMessagingConversionMapping.empresa_id == tenant_id,
            BusinessMessagingConversionMapping.trigger_type == trigger_type,
            BusinessMessagingConversionMapping.trigger_ref == trigger_ref,
            BusinessMessagingConversionMapping.event_name == event.event_name,
            BusinessMessagingConversionMapping.ativo.is_(True),
        ))
        if mapping is not None:
            return mapping, trigger_type, trigger_ref, source
        return None, trigger_type, trigger_ref, "active_mapping_missing"

    # Events without a persisted producer identity are eligible only if one
    # active mapping exists for this exact event and its allowed trigger type.
    allowed_types = HIGH_QUALITY_EVENT_CONTRACT.get(event.event_name, set())
    candidates = list((await session.execute(select(BusinessMessagingConversionMapping).where(
        BusinessMessagingConversionMapping.empresa_id == tenant_id,
        BusinessMessagingConversionMapping.event_name == event.event_name,
        BusinessMessagingConversionMapping.ativo.is_(True),
        BusinessMessagingConversionMapping.trigger_type.in_(list(allowed_types)) if allowed_types else False,
    ))).scalars().all())
    if len(candidates) == 1:
        return candidates[0], candidates[0].trigger_type, candidates[0].trigger_ref, source
    if len(candidates) > 1:
        return None, None, None, "mapping_resolution_ambiguous"
    return None, None, None, "active_mapping_missing"


async def _policy_mode(session: AsyncSession, event: CanonicalCommercialEvent, *, persist: bool) -> str:
    policy = await session.scalar(select(CommercialMetaEventPolicy).where(
        CommercialMetaEventPolicy.empresa_id == event.empresa_id,
        CommercialMetaEventPolicy.event_name == event.event_name,
    ))
    if policy is not None:
        return str(policy.mode or "disabled").lower()
    if event.event_name not in SUPPORTED_CANONICAL_EVENTS:
        return "disabled"
    if persist:
        statement = pg_insert(CommercialMetaEventPolicy).values(
            id=uuid.uuid4(), empresa_id=event.empresa_id,
            event_name=event.event_name, mode="shadow",
        ).on_conflict_do_nothing(constraint="uq_commercial_meta_policy_tenant_event")
        await session.execute(statement)
    return "shadow"


def _compare_payloads(
    *,
    event_name: str,
    canonical_event_id: str,
    canonical_item: dict[str, Any] | None,
    legacy: MetaConversionEvent | None,
    dataset_id_masked: str | None,
    tenant_id: Any,
    unsupported: bool,
    missing_fields: list[str],
    missing_attribution: bool,
) -> tuple[str, dict[str, Any]]:
    if unsupported:
        return "UNSUPPORTED_EVENT", {"reason": "unsupported_event"}
    if missing_fields:
        return "MISSING_REQUIRED_FIELDS", {"missing_fields": sorted(set(missing_fields))}
    if missing_attribution:
        return "MISSING_ATTRIBUTION", {"reason": "ctwa_attribution_missing"}
    if legacy is None:
        return "NO_LEGACY_REFERENCE", {"legacy_reference": "not_exact"}

    preview = legacy.payload_preview if isinstance(legacy.payload_preview, dict) else {}
    legacy_data = preview.get("data") if isinstance(preview.get("data"), list) else []
    legacy_item = legacy_data[0] if legacy_data and isinstance(legacy_data[0], dict) else {}
    legacy_user = legacy_item.get("user_data") if isinstance(legacy_item.get("user_data"), dict) else {}
    legacy_custom = legacy_item.get("custom_data") if isinstance(legacy_item.get("custom_data"), dict) else {}
    canonical_item = canonical_item or {}
    canonical_user = canonical_item.get("user_data") if isinstance(canonical_item.get("user_data"), dict) else {}
    canonical_custom = canonical_item.get("custom_data") if isinstance(canonical_item.get("custom_data"), dict) else {}
    fields = {
        "event_name": canonical_item.get("event_name") == legacy_item.get("event_name"),
        "event_id": canonical_event_id == str(legacy.event_id or legacy_item.get("event_id") or ""),
        "event_time": canonical_item.get("event_time") == legacy_item.get("event_time"),
        "action_source": canonical_item.get("action_source") == legacy_item.get("action_source"),
        "messaging_channel": canonical_item.get("messaging_channel") == legacy_item.get("messaging_channel"),
        "tenant_context": str(getattr(legacy, "empresa_id", tenant_id)) == str(tenant_id),
        "conversion_context": str(getattr(legacy, "conversion_context", "")) == "business_messaging_whatsapp",
        "dataset": _masked_id(dataset_id_masked) == _masked_id(legacy.dataset_id_masked),
        "waba": _masked_id(canonical_user.get("whatsapp_business_account_id")) == _masked_id(legacy_user.get("whatsapp_business_account_id")),
        "ctwa_present": bool(canonical_user.get("ctwa_clid")) == bool(legacy_user.get("ctwa_clid")),
        "ctwa_masked_match": _safe_payload_mask(canonical_user.get("ctwa_clid")) == legacy_user.get("ctwa_clid"),
        "value": _normalized_payload_value(canonical_custom.get("value")) == _normalized_payload_value(legacy_custom.get("value")),
        "currency": str(canonical_custom.get("currency") or "").upper() == str(legacy_custom.get("currency") or "").upper(),
    }
    if not fields["event_name"]:
        classification = "EVENT_NAME_MISMATCH"
    elif not fields["event_id"]:
        classification = "EVENT_ID_MISMATCH"
    elif not fields["value"] or not fields["currency"]:
        classification = "VALUE_MISMATCH"
    elif not all(fields[key] for key in ("action_source", "messaging_channel", "tenant_context", "conversion_context", "dataset", "waba")):
        classification = "CONTEXT_MISMATCH"
    elif not fields["ctwa_present"] or not fields["ctwa_masked_match"]:
        classification = "MISSING_ATTRIBUTION"
    elif not fields["event_time"] or not fields["currency"] or not fields["value"]:
        classification = "SEMANTICALLY_EQUIVALENT"
    elif legacy_user.get("ctwa_clid") or legacy_user.get("whatsapp_business_account_id"):
        # Legacy previews deliberately mask these values, so equality is only
        # semantic even when every masked fingerprint agrees.
        classification = "SEMANTICALLY_EQUIVALENT"
    else:
        classification = "EXACT"
    return classification, {"field_matches": fields}


async def project_commercial_event(
    session: AsyncSession,
    *,
    empresa_id: Any,
    commercial_event_id: Any,
    persist: bool = True,
) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    event_id = uuid.UUID(str(commercial_event_id))
    event = await session.scalar(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
        CanonicalCommercialEvent.id == event_id,
    ))
    if event is None:
        raise ValueError("commercial_event_not_found")
    policy_mode = await _policy_mode(session, event, persist=persist)
    canonical_identity_id = canonical_conversion_event_id(event)
    occurred = event.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    event_time = int(occurred.timestamp())
    legacy_class, legacy, legacy_candidates = await _legacy_reference(session, event)
    legacy_id = legacy.id if legacy_class == "EXACT_LINKED" and legacy is not None else None
    # Provider deduplication must share the immutable external id of the
    # already-sent JEV/CRM event when (and only when) reconciliation is exact.
    # The canonical identity remains separately recorded for auditability.
    canonical_id, canonical_event_id_source = canonical_delivery_event_id(
        event,
        canonical_event_id=canonical_identity_id,
        legacy_reference_classification=legacy_class,
        legacy_event=legacy,
    )
    unsupported = event.event_name not in BUSINESS_MESSAGING_EVENTS or event.event_name not in SUPPORTED_CANONICAL_EVENTS
    missing_fields: list[str] = []
    reason_code: str | None = None
    mapping: BusinessMessagingConversionMapping | None = None
    trigger_type: str | None = None
    trigger_ref: str | None = None
    trigger_source: str | None = None
    lead = None
    ctwa = None
    credential = None
    payload: dict[str, Any] | None = None
    item: dict[str, Any] | None = None
    summary: dict[str, Any] = {"historical_no_send": bool(event.is_backfilled or event.external_dispatch_policy == "historical_no_send")}

    if policy_mode == "disabled":
        projection_status, reason_code = "disabled", "event_policy_disabled"
    elif policy_mode == "live":
        # LIVE is represented in the control plane but rejected before any payload dispatch.
        projection_status, reason_code = "live_blocked", "p1c1_live_dispatch_disabled"
    elif unsupported:
        projection_status, reason_code = "unsupported", "unsupported_event_name"
    else:
        if event.event_name == "Purchase" and not (
            event.revenue_verified and event.payment_id and event.value is not None and event.currency
        ):
            missing_fields.append("verified_payment_evidence")
        mapping, trigger_type, trigger_ref, trigger_source = await _resolve_mapping(session, event)
        if mapping is None:
            missing_fields.append("business_messaging_mapping")
            known_mapping_errors = {"trigger_source_invalid", "canonical_tag_evidence_missing", "mapping_resolution_ambiguous", "active_mapping_missing"}
            reason_code = reason_code or (trigger_source if trigger_source in known_mapping_errors else "active_mapping_missing")
        else:
            if str(mapping.event_name or "") != event.event_name:
                missing_fields.append("mapping_event_name")
            try:
                normalized_source = normalize_trigger_source(trigger_source or "system")
            except ValueError:
                normalized_source = ""
                missing_fields.append("trigger_source")
            if normalized_source and normalized_source not in mapping_allowed_sources(mapping):
                missing_fields.append("trigger_source_allowed_by_mapping")
                reason_code = "trigger_source_not_allowed"
            if event.lead_id is None:
                missing_fields.append("lead")
            else:
                lead = await session.scalar(select(CRMLead).where(
                    CRMLead.empresa_id == tenant_id,
                    CRMLead.id == event.lead_id,
                ))
                if lead is None:
                    missing_fields.append("tenant_lead")
                else:
                    ctwa = _ctwa_clid(lead)
                    if not ctwa:
                        missing_fields.append("ctwa_clid")
                    try:
                        credential = await resolve_meta_conversion_credential(session, tenant_id, require_dataset=True)
                    except MetaCredentialResolutionError as exc:
                        error_code = str(exc)[:80]
                        missing_fields.append(_CREDENTIAL_ERROR_FIELDS.get(error_code, "business_messaging_credential"))
                        reason_code = reason_code or error_code
                    if credential is not None:
                        if credential.tenant_id != str(tenant_id) or credential.purpose != CONVERSIONS_PURPOSE:
                            missing_fields.append("tenant_scoped_business_messaging_credential")
                            reason_code = "business_messaging_credential_scope_invalid"
                        if not credential.waba_id:
                            missing_fields.append("waba_id")
                        if not credential.dataset_id:
                            missing_fields.append("dataset_id")
                    if not missing_fields:
                        domain_payload: dict[str, Any] = {}
                        if event.revenue_verified and event.value is not None and event.currency:
                            domain_payload = {"value": float(event.value), "currency": event.currency}
                        custom = _business_messaging_custom_data(lead, mapping, domain_payload, event.event_name)
                        payload = build_business_messaging_event(
                            event_name=event.event_name,
                            event_id=canonical_id,
                            credential=credential,
                            ctwa_clid=ctwa or "",
                            custom_data=custom,
                            event_time=event_time,
                        )
                        try:
                            validate_business_messaging_event_payload(payload)
                            item = payload["data"][0]
                            projection_status = "valid"
                            summary.update({
                                "payload_valid": True,
                                "mapping_trigger_type": trigger_type,
                                "credential_source": credential.credential_source,
                                "required_fields_valid": True,
                            })
                        except MetaConversionPayloadValidationError as exc:
                            missing_fields.append(exc.reason_code)
                            reason_code = exc.reason_code
                            projection_status = "invalid"
                    else:
                        projection_status = "invalid"
            if mapping is not None and event.lead_id is None:
                projection_status = "invalid"
        if missing_fields:
            projection_status = "invalid"
            summary.update({"payload_valid": False, "missing_fields": sorted(set(missing_fields))})
            reason_code = reason_code or "required_projection_fields_missing"

    comparison, comparison_json = _compare_payloads(
        event_name=event.event_name,
        canonical_event_id=canonical_id,
        canonical_item=item,
        legacy=legacy if legacy_class == "EXACT_LINKED" else None,
        dataset_id_masked=_masked_id(credential.dataset_id) if credential else None,
        tenant_id=tenant_id,
        unsupported=unsupported,
        missing_fields=missing_fields,
        missing_attribution=not bool(ctwa),
    )
    from app.services.meta.legacy_delivery_reconciliation import delivery_state_for_identity
    legacy_identity_state = await delivery_state_for_identity(
        session, event=event, legacy=legacy if legacy_class == "EXACT_LINKED" else None, credential=credential,
    )
    if legacy_class == "EXACT_LINKED" and legacy is not None:
        delivery_state = legacy_identity_state
    elif event.is_backfilled or event.external_dispatch_policy == "historical_no_send":
        delivery_state = "UNKNOWN_DELIVERY" if legacy_class in {"PROBABLE_ONLY", "AMBIGUOUS", "UNLINKED"} else "NOT_SENT"
    elif policy_mode == "shadow":
        delivery_state = "SHADOW_ONLY"
    elif policy_mode == "live":
        delivery_state = "BLOCKED"
    else:
        delivery_state = "NOT_SENT"

    dispatch_result = await ExternalConversionDispatcher().dispatch(
        mode=policy_mode,
        historical_no_send=bool(event.is_backfilled or event.external_dispatch_policy == "historical_no_send"),
        legacy_reference_classification=legacy_class,
        legacy_delivery_state=legacy_identity_state,
    )
    if dispatch_result["attempted"]:
        # Defensive assertion: the P1C.1 dispatcher can never report an attempt.
        raise RuntimeError("canonical_meta_shadow_dispatch_must_not_send")

    comparison_json = {
        **comparison_json,
        "legacy_reference_classification": legacy_class,
        "legacy_delivery_state": legacy_identity_state,
        "dispatch_attempted": False,
    }
    safe_summary = {
        **summary,
        "canonical_identity_id": canonical_identity_id,
        "delivery_event_id_source": canonical_event_id_source,
        "external_dispatch": dispatch_result["state"],
        "event_occurred": True,
        "projection_valid": projection_status == "valid",
        "ctwa_present": bool(ctwa),
        "attribution_status": str(event.attribution_status or "unknown"),
        "is_backfilled": bool(event.is_backfilled),
        "historical_no_send": bool(event.is_backfilled or event.external_dispatch_policy == "historical_no_send"),
    }
    eligibility = _eligibility_classification(
        projection_status=projection_status,
        missing_fields=missing_fields,
        unsupported=unsupported,
    )
    reason_categories = _reason_categories(missing_fields, unsupported=unsupported)
    safe_summary["eligibility_classification"] = eligibility
    safe_summary["reason_categories"] = reason_categories
    values = {
        "id": uuid.uuid4(),
        "empresa_id": tenant_id,
        "commercial_event_id": event.id,
        "legacy_event_id": legacy_id,
        "mapping_id": mapping.id if mapping else None,
        "projection_version": PROJECTION_VERSION,
        "canonical_event_id": canonical_id,
        "event_name": event.event_name,
        "mode": policy_mode,
        "projection_status": projection_status,
        "comparison_classification": comparison,
        "legacy_reference_classification": legacy_class,
        "delivery_state": delivery_state,
        "event_time": event_time,
        "action_source": "business_messaging" if item else None,
        "dataset_id_masked": _masked_id(credential.dataset_id) if credential else None,
        "waba_id_masked": _masked_id(credential.waba_id) if credential else None,
        "ctwa_present": bool(ctwa),
        "value": event.value if event.revenue_verified else None,
        "currency": event.currency if event.revenue_verified else None,
        "payload_hash": _payload_hash(payload) if payload else None,
        "reason_code": reason_code,
        "legacy_candidate_count": legacy_candidates,
        "comparison_json": comparison_json,
        "safe_summary_json": safe_summary,
        "last_projected_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    if persist:
        statement = pg_insert(CanonicalMetaConversionProjection).values(**values).on_conflict_do_update(
            constraint="uq_commercial_meta_projection_event_version",
            set_={key: value for key, value in values.items() if key not in {"id", "empresa_id", "commercial_event_id", "projection_version", "created_at"}},
        )
        await session.execute(statement)
        persisted_projection = await session.scalar(select(CanonicalMetaConversionProjection).where(
            CanonicalMetaConversionProjection.empresa_id == tenant_id,
            CanonicalMetaConversionProjection.commercial_event_id == event.id,
            CanonicalMetaConversionProjection.projection_version == PROJECTION_VERSION,
        ))
        if persisted_projection is None:
            raise RuntimeError("canonical_meta_projection_missing_after_upsert")
        from app.services.meta.conversion_outbox import ensure_shadow_outbox_intent

        await ensure_shadow_outbox_intent(
            session, event=event, projection=persisted_projection,
        )
    return {
        "commercial_event_id": str(event.id),
        "canonical_event_id": canonical_id,
        "event_name": event.event_name,
        "mode": policy_mode,
        "projection_status": projection_status,
        "comparison_classification": comparison,
        "legacy_reference_classification": legacy_class,
        "delivery_state": delivery_state,
        "eligibility_classification": eligibility,
        "reason_categories": reason_categories,
        "reason_code": reason_code,
        "payload_valid": projection_status == "valid",
        "dispatch_attempted": False,
        "legacy_candidate_count": legacy_candidates,
    }


async def run_shadow_projection(
    session: AsyncSession,
    *,
    empresa_id: Any,
    persist: bool,
) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    events = list((await session.execute(select(CanonicalCommercialEvent).where(
        CanonicalCommercialEvent.empresa_id == tenant_id,
    ).order_by(CanonicalCommercialEvent.occurred_at.asc(), CanonicalCommercialEvent.id.asc()))).scalars().all())
    counts: Counter[str] = Counter()
    by_event: dict[str, Counter[str]] = {}
    eligibility_counts: Counter[str] = Counter()
    reason_bucket_counts: Counter[str] = Counter()
    eligibility_by_event: dict[str, Counter[str]] = {}
    for event in events:
        result = await project_commercial_event(
            session, empresa_id=tenant_id, commercial_event_id=event.id, persist=persist,
        )
        counts["events"] += 1
        counts[result["projection_status"]] += 1
        counts[result["comparison_classification"]] += 1
        counts[result["legacy_reference_classification"]] += 1
        counts[result["delivery_state"]] += 1
        counts["payload_valid"] += int(bool(result["payload_valid"]))
        counts["dispatch_attempted"] += int(bool(result["dispatch_attempted"]))
        eligibility = str(result["eligibility_classification"])
        eligibility_counts[eligibility] += 1
        eligibility_by_event.setdefault(event.event_name, Counter())[eligibility] += 1
        for category in result["reason_categories"]:
            reason_bucket_counts[str(category)] += 1
        by_event.setdefault(event.event_name, Counter())[result["projection_status"]] += 1
    if persist:
        await session.flush()
    legacy_total = await session.scalar(select(func.count()).select_from(MetaConversionEvent).where(MetaConversionEvent.empresa_id == tenant_id)) or 0
    legacy_class_rows = (await session.execute(select(CommercialMetaEventLink.classification, func.count()).where(
        CommercialMetaEventLink.empresa_id == tenant_id,
    ).group_by(CommercialMetaEventLink.classification))).all()
    return {
        "tenant_event_count": len(events),
        "legacy_meta_event_count": int(legacy_total),
        "projection_status_counts": {key: int(value) for key, value in counts.items() if key in {"disabled", "valid", "invalid", "unsupported", "live_blocked"}},
        "comparison_counts": {key: int(value) for key, value in counts.items() if key in _COMPARISON_CLASSES},
        "legacy_reference_counts": {key: int(value) for key, value in counts.items() if key in {"EXACT_LINKED", "PROBABLE_ONLY", "AMBIGUOUS", "UNLINKED"}},
        "legacy_reconciliation_counts": {str(key): int(value) for key, value in legacy_class_rows},
        "delivery_state_counts": {key: int(value) for key, value in counts.items() if key in {"NOT_SENT", "SHADOW_ONLY", "ALREADY_DELIVERED_BY_LEGACY", "LEGACY_ACCEPTED", "LEGACY_FAILED", "UNKNOWN_DELIVERY", "NOT_APPLICABLE", "BLOCKED"}},
        "eligibility_counts": {key: int(value) for key, value in sorted(eligibility_counts.items())},
        "reason_bucket_counts": {key: int(value) for key, value in sorted(reason_bucket_counts.items())},
        "payload_valid_count": int(counts["payload_valid"]),
        "dispatch_attempted_count": int(counts["dispatch_attempted"]),
        "by_event_name": {name: dict(values) for name, values in sorted(by_event.items())},
        "eligibility_by_event_name": {name: dict(values) for name, values in sorted(eligibility_by_event.items())},
        "persisted": bool(persist),
        "meta_graph_write_executed": False,
    }


async def project_commercial_event_best_effort(session: AsyncSession, *, empresa_id: Any, commercial_event_id: Any) -> None:
    """Keep shadow failures isolated from canonical business fact persistence."""
    try:
        async with session.begin_nested():
            await project_commercial_event(
                session, empresa_id=empresa_id, commercial_event_id=commercial_event_id, persist=True,
            )
    except Exception as exc:
        logger.warning(
            "COMMERCIAL_META_SHADOW_PROJECTION_FAILED",
            extra={"reason_type": type(exc).__name__, "tenant_scoped": True},
        )


async def shadow_status(session: AsyncSession, *, empresa_id: Any) -> dict[str, Any]:
    """Read-only, tenant-scoped status for future Admin Health integration."""
    tenant_id = uuid.UUID(str(empresa_id))
    canonical_count = int(await session.scalar(select(func.count()).select_from(CanonicalCommercialEvent).where(CanonicalCommercialEvent.empresa_id == tenant_id)) or 0)
    projection_rows = (await session.execute(select(CanonicalMetaConversionProjection.projection_status, CanonicalMetaConversionProjection.comparison_classification, CanonicalMetaConversionProjection.delivery_state, CanonicalMetaConversionProjection.safe_summary_json).where(
        CanonicalMetaConversionProjection.empresa_id == tenant_id,
    ))).all()
    status_counts: Counter[str] = Counter()
    comparison_counts: Counter[str] = Counter()
    delivery_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    reason_bucket_counts: Counter[str] = Counter()
    for status, comparison, delivery, safe_summary in projection_rows:
        status_counts[str(status)] += 1
        comparison_counts[str(comparison)] += 1
        delivery_counts[str(delivery)] += 1
        safe_summary = safe_summary if isinstance(safe_summary, dict) else {}
        missing_fields = safe_summary.get("missing_fields") if isinstance(safe_summary.get("missing_fields"), list) else []
        eligibility = str(safe_summary.get("eligibility_classification") or _eligibility_classification(
            projection_status=str(status),
            missing_fields=[str(value) for value in missing_fields],
            unsupported=str(status) == "unsupported",
        ))
        eligibility_counts[eligibility] += 1
        categories = safe_summary.get("reason_categories") if isinstance(safe_summary.get("reason_categories"), list) else _reason_categories(
            [str(value) for value in missing_fields], unsupported=str(status) == "unsupported",
        )
        for category in categories:
            reason_bucket_counts[str(category)] += 1
    legacy_total = int(await session.scalar(select(func.count()).select_from(MetaConversionEvent).where(MetaConversionEvent.empresa_id == tenant_id)) or 0)
    legacy_rows = (await session.execute(select(CommercialMetaEventLink.classification, func.count()).where(
        CommercialMetaEventLink.empresa_id == tenant_id,
    ).group_by(CommercialMetaEventLink.classification))).all()
    legacy_classes = {str(key): int(value) for key, value in legacy_rows}
    last_shadow = await session.scalar(select(func.max(CanonicalMetaConversionProjection.last_projected_at)).where(CanonicalMetaConversionProjection.empresa_id == tenant_id))
    last_legacy_send = await session.scalar(select(func.max(MetaConversionEvent.last_attempt_at)).where(
        MetaConversionEvent.empresa_id == tenant_id,
        MetaConversionEvent.status.in_(["sent", "accepted"]),
    ))
    from app.services.meta.conversion_outbox import conversion_outbox_status

    outbox_status = await conversion_outbox_status(session, empresa_id=tenant_id)
    from app.services.meta.legacy_delivery_evidence import legacy_delivery_evidence_status
    evidence_status = await legacy_delivery_evidence_status(session, tenant_id)
    return {
        "canonical_event_count": canonical_count,
        "shadow_eligible": int(eligibility_counts["VALID_PAYLOAD_SHADOW"]),
        "shadow_invalid": int(eligibility_counts["INVALID_MISSING_FIELDS"]),
        "shadow_ineligible": int(sum(value for key, value in eligibility_counts.items() if key.startswith("INELIGIBLE_"))),
        "shadow_configuration_blocked": int(eligibility_counts["CONFIGURATION_BLOCKED"]),
        "shadow_raw_projection_invalid": int(status_counts["invalid"]),
        "shadow_unsupported": int(eligibility_counts["INELIGIBLE_UNSUPPORTED_EVENT"]),
        "shadow_eligibility_classification": {key: int(value) for key, value in sorted(eligibility_counts.items())},
        "invalid_reason_breakdown": {key: int(value) for key, value in sorted(reason_bucket_counts.items())},
        "shadow_disabled": int(status_counts["disabled"]),
        "shadow_live_blocked": int(status_counts["live_blocked"]),
        "legacy_meta_event_count": legacy_total,
        "legacy_delivery_exact": int(delivery_counts["ALREADY_DELIVERED_BY_LEGACY"]),
        "legacy_delivery_failed": int(delivery_counts["LEGACY_FAILED"]),
        "unknown_delivery": int(delivery_counts["UNKNOWN_DELIVERY"]),
        "legacy_reconciliation": legacy_classes,
        "payload_comparison": {key: int(value) for key, value in comparison_counts.items()},
        "last_shadow_at": last_shadow.isoformat() if last_shadow else None,
        "last_legacy_send_at": last_legacy_send.isoformat() if last_legacy_send else None,
        "duplicate_risk": int(delivery_counts["UNKNOWN_DELIVERY"]),
        "unresolved_linkage": int(legacy_classes.get("LINKED_PROBABLE", 0) + legacy_classes.get("AMBIGUOUS", 0) + legacy_classes.get("UNLINKED", 0)),
        "external_dispatch_mode": "SHADOW_OR_DISABLED_LIVE_BLOCKED",
        "conversion_outbox": outbox_status,
        "legacy_delivery_evidence": evidence_status,
        "cutover_blockers": ["LIVE_NOT_AUTHORIZED", "LEGACY_OWNERSHIP_NOT_ENFORCED", "PROBABLE_LINKAGE_REQUIRES_REVIEW", "NATURAL_EVENT_CANARY_REQUIRED"],
        "tenant_scoped": True,
    }

