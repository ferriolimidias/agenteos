from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_sanitizer import sanitize_for_log
from app.services.meta_attribution import META_ATTRIBUTION_KEY
from app.services.meta.conversion_credentials import (
    CONVERSIONS_PURPOSE, MetaCredentialResolutionError, ResolvedMetaCredential,
)
from app.services.meta.business_messaging_conversions import resolve_meta_conversion_credential
from app.services.meta.dataset_events import BUSINESS_MESSAGING_EVENTS, BUSINESS_MESSAGING_WHATSAPP, build_business_messaging_event
from db.models import CRMLead, Empresa, MetaConversionEvent, TagCRM

logger = logging.getLogger(__name__)

GRAPH_CAPI_VERSION = "v19.0"
VALID_SEND_MODES = {"disabled", "dry_run", "test_event", "live"}
CURRENT_CONVERSION_CONTEXT = "current"


def business_messaging_test_enabled() -> bool:
    return str(os.getenv("META_BUSINESS_MESSAGING_TEST_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"}
def _digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalizar_telefone_para_meta_ph(telefone: str | None) -> str | None:
    digits = _digits_only(telefone)
    if not digits:
        return None
    if digits.startswith("55"):
        return digits
    if 10 <= len(digits) <= 11 and not digits.startswith("0"):
        return "55" + digits
    return digits


def _sha256_lower(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def hashear_ph_meta(telefone_normalizado: str) -> str:
    return _sha256_lower(telefone_normalizado)


def mascarar_secret(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) <= 8:
        return f"{raw[:2]}***{raw[-2:]}"
    return f"{raw[:4]}...{raw[-4:]}"


def gerar_event_id_estavel(empresa_id: Any, lead_id: Any, tag_id: Any, event_name: str) -> str:
    return f"{empresa_id}:{lead_id}:{tag_id}:{str(event_name or '').strip()}"


def gerar_event_id_auditoria(prefix: str, stable_live_key: str) -> str:
    return f"{prefix}:{stable_live_key}:{uuid.uuid4()}"


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dados_adicionais(lead: CRMLead) -> dict[str, Any]:
    dados = getattr(lead, "dados_adicionais", None)
    return dados if isinstance(dados, dict) else {}


def _first_extra(dados: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = dados.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _montar_fbc(lead: CRMLead, dados: dict[str, Any]) -> str | None:
    fbc = _first_extra(dados, ("fbc", "_fbc"))
    if fbc:
        return fbc
    fbclid = str(getattr(lead, "fbclid", "") or dados.get("fbclid") or "").strip()
    if fbclid:
        return f"fb.1.{int(time.time() * 1000)}.{fbclid}"
    return None


def _custom_data(lead: CRMLead, config: Any | None, dados: dict[str, Any]) -> dict[str, Any]:
    custom: dict[str, Any] = {}
    currency = str(getattr(config, "currency", "") or "BRL").strip().upper()
    value = getattr(config, "default_value", None)
    if value is None:
        value = getattr(lead, "valor_conversao", None)
    if value is not None:
        try:
            custom["value"] = float(value)
            custom["currency"] = currency
        except (TypeError, ValueError):
            pass

    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "campaign", "adset", "ad"):
        value = dados.get(key)
        if value is not None and str(value).strip():
            custom[key] = str(value).strip()[:255]
    return custom


def _ctwa_clid(dados: dict[str, Any]) -> str | None:
    attribution = dados.get(META_ATTRIBUTION_KEY)
    last_touch = attribution.get("last_touch") if isinstance(attribution, dict) else None
    value = last_touch.get("ctwa_clid") if isinstance(last_touch, dict) else None
    return value if isinstance(value, str) and value != "" else None


async def montar_payload_meta_capi(
    lead: CRMLead,
    tag: TagCRM | None,
    empresa: Empresa,
    config: Any | None = None,
    *,
    event_name: str | None = None,
    event_id: str | None = None,
    test_event_code: str | None = None,
    credential: ResolvedMetaCredential | None = None,
) -> dict[str, Any]:
    resolved_event_name = str(event_name or getattr(config, "event_name", "") or "Lead").strip()
    if not resolved_event_name:
        raise ValueError("event_name obrigatório para Meta CAPI.")

    tag_id = getattr(tag, "id", None) or getattr(config, "tag_id", None) or uuid.UUID(int=0)
    resolved_event_id = event_id or gerar_event_id_estavel(empresa.id, lead.id, tag_id, resolved_event_name)
    dados = _dados_adicionais(lead)
    context = str(getattr(config, "conversion_context", CURRENT_CONVERSION_CONTEXT) or CURRENT_CONVERSION_CONTEXT)
    if context == BUSINESS_MESSAGING_WHATSAPP:
        if credential is None:
            raise ValueError("business_messaging_credential_required")
        payload = build_business_messaging_event(
            event_name=resolved_event_name,
            event_id=resolved_event_id,
            credential=credential,
            ctwa_clid=_ctwa_clid(dados) or "",
            custom_data=_custom_data(lead, config, dados),
            partner_agent=str(os.getenv("META_PARTNER_AGENT") or "").strip() or None,
            event_time=int(time.time()),
        )
        if test_event_code:
            payload["test_event_code"] = str(test_event_code).strip()
        return payload
    tel_norm = normalizar_telefone_para_meta_ph(getattr(lead, "telefone_contato", None))
    if not tel_norm:
        raise ValueError("telefone inválido para Meta CAPI.")

    user_data: dict[str, Any] = {"ph": [hashear_ph_meta(tel_norm)]}
    email = _first_extra(dados, ("email", "e-mail", "mail"))
    if email:
        user_data["em"] = [_sha256_lower(email)]

    fbp = _first_extra(dados, ("fbp", "_fbp"))
    if fbp:
        user_data["fbp"] = fbp
    fbc = _montar_fbc(lead, dados)
    if fbc:
        user_data["fbc"] = fbc
    ip = _first_extra(dados, ("client_ip_address", "ip", "ip_address"))
    if ip:
        user_data["client_ip_address"] = ip
    user_agent = _first_extra(dados, ("client_user_agent", "user_agent", "ua"))
    if user_agent:
        user_data["client_user_agent"] = user_agent

    item: dict[str, Any] = {
        "event_name": resolved_event_name,
        "event_time": int(time.time()),
        "event_id": resolved_event_id,
        "action_source": "chat",
        "user_data": user_data,
    }
    event_source_url = _first_extra(dados, ("event_source_url", "source_url", "url_origem"))
    if event_source_url:
        item["event_source_url"] = event_source_url
    custom = _custom_data(lead, config, dados)
    if custom:
        item["custom_data"] = custom

    payload: dict[str, Any] = {"data": [item]}
    if test_event_code:
        payload["test_event_code"] = str(test_event_code).strip()
    return payload


def payload_preview_seguro(payload: dict[str, Any]) -> dict[str, Any]:
    preview = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(preview, dict) and preview.pop("test_event_code", None):
        preview["test_event_code_present"] = True
    for item in preview.get("data", []) if isinstance(preview, dict) else []:
        user_data = item.get("user_data") if isinstance(item, dict) else None
        if not isinstance(user_data, dict):
            continue
        for key in ("ph", "em"):
            if key in user_data:
                values = user_data.get(key) or []
                user_data[key] = [mascarar_secret(str(v)) for v in values]
        for key in ("fbc", "fbp", "client_ip_address", "client_user_agent"):
            if key in user_data:
                user_data[key] = mascarar_secret(str(user_data[key]))
        for key in ("ctwa_clid", "whatsapp_business_account_id"):
            if key in user_data:
                user_data[key] = mascarar_secret(str(user_data[key]))
    return preview


def attribution_preview_seguro(lead: CRMLead) -> dict[str, Any] | None:
    """Sanitized evidence for dry-run; never part of the traditional CAPI body."""
    ctwa_clid = _ctwa_clid(_dados_adicionais(lead))
    if not ctwa_clid:
        return None
    return {"messaging_channel": "whatsapp", "ctwa_clid": mascarar_secret(ctwa_clid)}


def _response_preview_seguro(response: httpx.Response | None = None, body: Any = None) -> dict[str, Any] | None:
    if response is None and body is None:
        return None
    return {
        "status_code": getattr(response, "status_code", None),
        "body": body,
    }


async def registrar_evento_meta_capi(
    session: AsyncSession,
    *,
    empresa: Empresa,
    lead: CRMLead | None,
    tag: TagCRM | None,
    config: Any | None,
    event_name: str,
    event_id: str,
    status: str,
    reservation_key: str | None = None,
    payload: dict[str, Any] | None = None,
    response_preview: dict[str, Any] | None = None,
    error_message: str | None = None,
    test_event_code: str | None = None,
    source: str = "tag",
    attempts: int = 0,
    attribution_preview: dict[str, Any] | None = None,
    conversion_context: str = CURRENT_CONVERSION_CONTEXT,
    credential: ResolvedMetaCredential | None = None,
    ctwa_present: bool = False,
    trigger_type: str | None = None,
    trigger_ref: str | None = None,
    trigger_source: str | None = None,
    trigger_source_event_id: str | None = None,
) -> MetaConversionEvent:
    event = MetaConversionEvent(
        empresa_id=empresa.id,
        lead_id=getattr(lead, "id", None),
        tag_id=getattr(tag, "id", None),
        event_name=event_name,
        event_id=event_id,
        reservation_key=reservation_key,
        status=status,
        payload_hash=_payload_hash(payload or {}) if payload is not None else None,
        payload_preview={
            **payload_preview_seguro(payload or {}),
            **({"attribution": attribution_preview} if attribution_preview else {}),
        },
        response_preview=response_preview,
        error_message=(str(error_message)[:2000] if error_message else None),
        attempts=attempts,
        last_attempt_at=datetime.utcnow() if attempts else None,
        test_event_code=test_event_code,
        source=source,
        conversion_context=conversion_context,
        action_source="business_messaging" if conversion_context == BUSINESS_MESSAGING_WHATSAPP else "chat",
        messaging_channel="whatsapp" if conversion_context == BUSINESS_MESSAGING_WHATSAPP else None,
        credential_source=getattr(credential, "credential_source", None),
        waba_id_masked=mascarar_secret(getattr(credential, "waba_id", None)),
        dataset_id_masked=mascarar_secret(getattr(credential, "dataset_id", None)),
        ctwa_present=ctwa_present,
        trigger_type=trigger_type,
        trigger_ref=trigger_ref,
        trigger_source=trigger_source,
        trigger_source_event_id=trigger_source_event_id,
    )
    session.add(event)
    await session.flush()
    return event


async def reservar_evento_meta_capi_live(
    session: AsyncSession,
    *,
    empresa: Empresa,
    lead: CRMLead,
    tag: TagCRM,
    config: Any,
    event_name: str,
    event_id: str,
    reservation_key: str,
    payload: dict[str, Any],
    source: str,
    trigger_source: str | None = None,
) -> tuple[MetaConversionEvent | None, str | None]:
    empresa_id = empresa.id
    lead_id = lead.id
    tag_id = getattr(tag, "id", None)
    event = MetaConversionEvent(
        empresa_id=empresa_id,
        lead_id=lead_id,
        tag_id=tag_id,
        event_name=event_name,
        event_id=event_id,
        reservation_key=reservation_key,
        status="pending",
        payload_hash=_payload_hash(payload),
        payload_preview=payload_preview_seguro(payload),
        attempts=0,
        source=source,
        trigger_source=trigger_source,
    )
    session.add(event)
    try:
        await session.flush()
        await session.commit()
        return event, None
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(MetaConversionEvent).where(MetaConversionEvent.reservation_key == reservation_key)
            )
        ).scalars().first()
        existing_status = str(getattr(existing, "status", "") or "")
        if existing_status == "sent":
            reason = "duplicate_sent"
        elif existing_status == "pending":
            reason = "pending_exists"
        elif existing_status == "failed":
            reason = "failed_exists"
        else:
            reason = "duplicate_event_reserved"
        logger.info(
            "[META CAPI] duplicate_event_reserved | empresa_id=%s lead_id=%s tag_id=%s event_id_hash=%s status=%s",
            empresa_id,
            lead_id,
            tag_id,
            hashlib.sha256(str(reservation_key).encode("utf-8")).hexdigest()[:16],
            existing_status or "unknown",
        )
        return None, reason


async def atualizar_reserva_meta_capi(
    session: AsyncSession,
    event: MetaConversionEvent,
    *,
    status: str,
    response_preview: dict[str, Any] | None = None,
    error_message: str | None = None,
    attempts: int | None = None,
) -> None:
    event.status = status
    event.response_preview = response_preview
    event.error_message = str(error_message)[:2000] if error_message else None
    event.attempts = int(attempts) if attempts is not None else int(event.attempts or 0) + 1
    event.last_attempt_at = datetime.utcnow() if event.attempts else None
    event.atualizado_em = datetime.utcnow()
    session.add(event)
    await session.flush()
    await session.commit()


async def _prepare_delivery_evidence(
    *, empresa_id: Any, dataset_id: str, waba_id: str | None,
    payload: dict[str, Any], transport_path: str,
) -> list[Any]:
    """Observe existing requests; failed telemetry never authorizes or blocks send."""
    evidence_ids = []
    try:
        from app.services.meta.legacy_delivery_evidence import start_delivery_attempt

        correlation_id = uuid.uuid4()
        for item in payload.get("data", []) if isinstance(payload.get("data"), list) else []:
            if not isinstance(item, dict):
                continue
            evidence_id = await start_delivery_attempt(
                empresa_id=empresa_id, dataset_id=dataset_id, waba_id=waba_id,
                event_name=str(item.get("event_name") or ""),
                external_event_id=str(item.get("event_id") or ""),
                transport_path=transport_path, request_correlation_id=correlation_id,
            )
            if evidence_id is not None:
                evidence_ids.append(evidence_id)
    except Exception as exc:
        logger.warning("LEGACY_DELIVERY_EVIDENCE_INCOMPLETE phase=prepare error_type=%s", type(exc).__name__)
    return evidence_ids


async def _mark_request_evidence(evidence_ids: list[Any]) -> None:
    try:
        from app.services.meta.legacy_delivery_evidence import mark_delivery_attempted

        for evidence_id in evidence_ids:
            await mark_delivery_attempted(evidence_id)
    except Exception as exc:
        logger.warning("LEGACY_DELIVERY_EVIDENCE_INCOMPLETE phase=request error_type=%s", type(exc).__name__)


async def _finish_request_evidence(
    evidence_ids: list[Any], *, http_status: int | None = None,
    provider_response: Any = None, error_phase: str | None = None,
    expected_event_count: int = 1,
) -> None:
    try:
        from app.services.meta.legacy_delivery_evidence import finish_delivery_attempt

        # Aggregate acknowledgement cannot prove which member of a partial batch arrived.
        evidence_response = provider_response
        if isinstance(provider_response, dict) and provider_response.get("events_received") != expected_event_count:
            evidence_response = {key: value for key, value in provider_response.items() if key != "events_received"}
        for evidence_id in evidence_ids:
            await finish_delivery_attempt(
                evidence_id, http_status=http_status,
                provider_response=evidence_response, error_phase=error_phase,
            )
    except Exception as exc:
        logger.warning("LEGACY_DELIVERY_EVIDENCE_INCOMPLETE phase=finish error_type=%s", type(exc).__name__)


async def enviar_payload_meta_capi(
    empresa: Empresa,
    payload: dict[str, Any],
) -> tuple[int, Any]:
    pixel_id = str(getattr(empresa, "meta_pixel_id", "") or "").strip()
    access_token = str(getattr(empresa, "meta_access_token", "") or "").strip()
    if not pixel_id or not access_token:
        raise ValueError("meta_pixel_id/meta_access_token ausentes.")
    url = f"https://graph.facebook.com/{GRAPH_CAPI_VERSION}/{pixel_id}/events"
    evidence_ids = await _prepare_delivery_evidence(
        empresa_id=empresa.id, dataset_id=pixel_id, waba_id=None,
        payload=payload, transport_path="ads_integration_service.enviar_payload_meta_capi",
    )
    request_entered = False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await _mark_request_evidence(evidence_ids)
            request_entered = True
            response = await client.post(url, params={"access_token": access_token}, json=payload)
    except Exception:
        await _finish_request_evidence(evidence_ids, error_phase="after_request" if request_entered else "before_request")
        raise
    try:
        body: Any = response.json()
    except Exception:
        body = response.text[:1000]
    body = sanitize_for_log(body)
    await _finish_request_evidence(evidence_ids, http_status=response.status_code, provider_response=body, expected_event_count=len(payload["data"]) if isinstance(payload, dict) and isinstance(payload.get("data"), list) else 0)
    return response.status_code, body


async def enviar_payload_meta_business_messaging(
    credential: ResolvedMetaCredential,
    payload: dict[str, Any],
) -> tuple[int, Any]:
    """Send one Dataset API request with a tenant-scoped conversion credential."""
    if credential.purpose != CONVERSIONS_PURPOSE or not credential.dataset_id:
        raise ValueError("business_messaging_credential_invalid")
    url = f"https://graph.facebook.com/{credential.graph_version}/{credential.dataset_id}/events"
    evidence_ids = await _prepare_delivery_evidence(
        empresa_id=credential.tenant_id, dataset_id=credential.dataset_id,
        waba_id=credential.waba_id, payload=payload,
        transport_path="ads_integration_service.enviar_payload_meta_business_messaging",
    )
    request_entered = False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await _mark_request_evidence(evidence_ids)
            request_entered = True
            response = await client.post(url, headers={"Authorization": f"Bearer {credential.access_token}"}, json=payload)
    except Exception:
        await _finish_request_evidence(evidence_ids, error_phase="after_request" if request_entered else "before_request")
        raise
    try:
        body: Any = response.json()
    except Exception:
        body = response.text[:1000]
    body = sanitize_for_log(body)
    await _finish_request_evidence(evidence_ids, http_status=response.status_code, provider_response=body, expected_event_count=len(payload["data"]) if isinstance(payload, dict) and isinstance(payload.get("data"), list) else 0)
    return response.status_code, body


MAX_CAPI_ATTEMPTS = 3
CAPI_RETRY_BASE_SECONDS = 0.25


def _meta_error_fields(body: Any) -> dict[str, Any]:
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def classify_meta_capi_retry(status_code: int, body: Any, exc: BaseException | None = None) -> tuple[bool, str, bool]:
    """Return (retryable, reason_code, is_transient) without retrying permanent 4xx errors."""
    if exc is not None:
        retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))
        return retryable, "META_NETWORK_TRANSIENT" if retryable else "META_TRANSPORT_ERROR", retryable
    error = _meta_error_fields(body)
    is_transient = bool(error.get("is_transient"))
    if is_transient:
        return True, "META_TRANSIENT_ERROR", True
    if status_code == 429:
        return True, "META_RATE_LIMITED", True
    if status_code >= 500:
        return True, "META_HTTP_5XX", True
    if status_code >= 400:
        return False, f"META_HTTP_{status_code}", False
    return False, "META_ACCEPTED", False


def _meta_attempt_preview(status_code: int, body: Any) -> dict[str, Any]:
    error = _meta_error_fields(body)
    return {
        "status_code": status_code,
        "events_received": body.get("events_received") if isinstance(body, dict) else None,
        "messages": body.get("messages") if isinstance(body, dict) else None,
        "fbtrace_id_present": bool(body.get("fbtrace_id")) if isinstance(body, dict) else False,
        "error": {
            key: error.get(key)
            for key in ("type", "code", "error_subcode", "message", "error_user_title", "error_user_msg", "error_data", "is_transient")
            if error.get(key) is not None
        },
        "body": body,
    }


async def enviar_payload_meta_business_messaging_with_retry(
    credential: ResolvedMetaCredential,
    payload: dict[str, Any],
    *,
    event_id: str,
    event_name: str,
) -> tuple[int, Any, int, str, dict[str, Any]]:
    """Send the existing request with bounded retries and an immutable payload/event_id."""
    last_status = 0
    last_body: Any = None
    last_reason = "META_TRANSPORT_ERROR"
    last_preview: dict[str, Any] = {}
    for attempt in range(1, MAX_CAPI_ATTEMPTS + 1):
        started = time.monotonic()
        exc: BaseException | None = None
        try:
            status_code, body = await enviar_payload_meta_business_messaging(credential, payload)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as error:
            status_code, body, exc = 0, {"error": {"type": error.__class__.__name__, "message": str(error)[:300]}}, error
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        retryable, reason_code, is_transient = classify_meta_capi_retry(status_code, body, exc)
        preview = _meta_attempt_preview(status_code, body)
        preview["latency_ms"] = latency_ms
        preview["attempt_number"] = attempt
        preview["reason_code"] = reason_code
        logger.info(
            "[META CAPI] attempt event_id_hash=%s event_name=%s attempt=%s status=%s meta_code=%s meta_subcode=%s is_transient=%s events_received=%s latency_ms=%s fbtrace_id_present=%s reason_code=%s",
            hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()[:16],
            event_name,
            attempt,
            status_code,
            preview["error"].get("code"),
            preview["error"].get("error_subcode"),
            is_transient,
            preview.get("events_received"),
            latency_ms,
            preview.get("fbtrace_id_present"),
            reason_code,
        )
        last_status, last_body, last_reason, last_preview = status_code, body, reason_code, preview
        if not retryable or attempt >= MAX_CAPI_ATTEMPTS:
            return last_status, last_body, attempt, last_reason, last_preview
        delay = CAPI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, CAPI_RETRY_BASE_SECONDS)
        await asyncio.sleep(delay)
    return last_status, last_body, MAX_CAPI_ATTEMPTS, last_reason, last_preview
