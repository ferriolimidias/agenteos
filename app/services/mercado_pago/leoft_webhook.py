from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified


class LeoFTMercadoPagoWebhookError(ValueError):
    pass


def validate_signature(*, raw_body: bytes, x_signature: str | None, x_request_id: str | None, secret: str | None) -> bool:
    secret_value = os.getenv("MERCADO_PAGO_LEOFT_WEBHOOK_SECRET") if secret is None else secret
    secret_text = str(secret_value or "").strip()
    if not secret_text or not x_signature or not x_request_id:
        return False
    parts = dict(item.split("=", 1) for item in str(x_signature).split(",") if "=" in item)
    timestamp = str(parts.get("ts") or "").strip()
    received = str(parts.get("v1") or "").strip()
    if not timestamp or not received:
        return False
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = {}
    payment_id = str(((data.get("data") or {}).get("id")) or "").strip()
    manifest = f"id:{payment_id};request-id:{str(x_request_id).strip()};ts:{timestamp};"
    expected = hmac.new(secret_text.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def extract_payment_id(payload: Mapping[str, Any]) -> str:
    payment_id = str(((payload.get("data") or {}).get("id")) or payload.get("id") or "").strip()
    if str(payload.get("type") or payload.get("topic") or "").strip().lower() != "payment":
        raise LeoFTMercadoPagoWebhookError("unsupported_event_type")
    if not payment_id:
        raise LeoFTMercadoPagoWebhookError("payment_id_missing")
    return payment_id


async def lookup_payment(payment_id: str, *, access_token: str | None = None) -> dict[str, Any]:
    token_value = os.getenv("MERCADO_PAGO_LEOFT_ACCESS_TOKEN") if access_token is None else access_token
    token = str(token_value or "").strip()
    if not token:
        raise LeoFTMercadoPagoWebhookError("access_token_missing")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        response = await client.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise LeoFTMercadoPagoWebhookError(f"payment_lookup_http_{response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise LeoFTMercadoPagoWebhookError("payment_lookup_invalid_body")
    return body


async def persist_payment_result(
    db,
    payment: Mapping[str, Any],
    *,
    expected_empresa_id: str | None = None,
) -> dict[str, Any]:
    from db.models import CRMLead, LeoFTIntegrationEvent
    from app.services.leoft_funnel_events import record_leoft_funnel_event
    payment_id = str(payment.get("id") or "").strip()
    status = str(payment.get("status") or "").strip().lower()
    if not payment_id:
        raise LeoFTMercadoPagoWebhookError("payment_id_missing")
    if status not in {"approved", "pending", "in_process", "rejected", "cancelled", "canceled", "refunded"}:
        raise LeoFTMercadoPagoWebhookError("unsupported_payment_status")
    metadata = payment.get("metadata") if isinstance(payment.get("metadata"), Mapping) else {}
    empresa_id = str(metadata.get("empresa_id") or "").strip()
    lead_id = str(metadata.get("lead_id") or "").strip()
    anamnese_id = str(metadata.get("anamnese_id") or "").strip()
    reference = str(payment.get("external_reference") or "").strip()
    if not empresa_id or not lead_id or not anamnese_id or not reference:
        raise LeoFTMercadoPagoWebhookError("payment_correlation_missing")
    if expected_empresa_id is not None and empresa_id != str(expected_empresa_id):
        raise LeoFTMercadoPagoWebhookError("payment_tenant_mismatch")
    try:
        lead = await db.get(CRMLead, UUID(lead_id))
    except (ValueError, TypeError):
        lead = None
    if lead is None or str(lead.empresa_id) != empresa_id:
        raise LeoFTMercadoPagoWebhookError("payment_tenant_mismatch")
    dados = dict(lead.dados_adicionais or {})
    anamnese = dict((dados.get("anamneses") or {}).get("leoft") or {})
    checkout = dict(anamnese.get("checkout") or {})
    if str(checkout.get("reference") or "") != reference:
        raise LeoFTMercadoPagoWebhookError("payment_reference_mismatch")

    idempotency_key = f"mercado_pago:payment:{payment_id}:{status}"
    existing = await db.scalar(select(LeoFTIntegrationEvent).where(
        LeoFTIntegrationEvent.integration == "mercado_pago_leoft_payment",
        LeoFTIntegrationEvent.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        return {"ok": True, "status": "duplicate", "payment_id": payment_id, "payment_status": status}

    event_payload = {
        "event": "leoft.payment_approved" if status == "approved" else ("leoft.payment_refunded" if status == "refunded" else "leoft.payment_status"),
        "empresa_id": empresa_id,
        "lead_id": lead_id,
        "anamnese_id": anamnese_id,
        "payment_id": payment_id,
        "checkout_reference": reference,
        "product": "Método Híbrido LeoFT",
        "amount": payment.get("transaction_amount"),
        "currency": payment.get("currency_id"),
        "payment_status": status,
        "approved_at": payment.get("date_approved") if status == "approved" else None,
    }
    if status == "approved":
        leoft = dict(dados.get("leoft") or {})
        leoft["payment"] = {
            "payment_id": payment_id,
            "status": "approved",
            "provider": "mercado_pago",
            "external_reference": reference,
            "amount": payment.get("transaction_amount"),
            "currency": payment.get("currency_id"),
            "approved_at": payment.get("date_approved"),
        }
        dados["leoft"] = leoft
        lead.dados_adicionais = dados
        flag_modified(lead, "dados_adicionais")
        try:
            from app.services.leoft_sale_fulfillment_service import fulfill_sale_confirmed

            fulfillment = await fulfill_sale_confirmed(
                db,
                payment=payment,
                lead=lead,
                anamnese_id=anamnese_id,
                environment=str(metadata.get("environment") or "production"),
            )
            event_payload["fulfillment"] = {
                "fulfillment_id": fulfillment.get("fulfillment_id"),
                "status": fulfillment.get("status"),
                "stage": fulfillment.get("stage"),
                "student_id": fulfillment.get("student_id"),
                "access_id": fulfillment.get("access_id"),
                "student_program_id": fulfillment.get("student_program_id"),
            }
        except Exception as fulfillment_exc:
            # Payment acceptance remains durable; the fulfillment record is
            # retryable and exposes the sanitized failure state.
            event_payload["fulfillment_error"] = fulfillment_exc.__class__.__name__
        try:
            from app.services.meta.business_messaging_conversion_engine import (
                ConversionDomainEvent,
                emit_conversion_domain_event,
            )

            event_time = _payment_event_epoch(payment)
            await emit_conversion_domain_event(
                db,
                ConversionDomainEvent(
                    empresa_id=UUID(empresa_id),
                    event_type="payment_confirmed",
                    lead_id=UUID(lead_id),
                    trigger_ref="mercado_pago_approved",
                    source_event_id=f"mercado_pago:{payment_id}",
                    trigger_source="integration",
                    event_time=event_time,
                    payload={
                        "provider": "mercado_pago",
                        "external_payment_id": payment_id,
                        "payment_id": payment_id,
                        "transaction_id": str(payment.get("transaction_id") or payment.get("operation_id") or "") or None,
                        "amount": payment.get("transaction_amount"),
                        "currency": payment.get("currency_id"),
                        "approved_at": payment.get("date_approved"),
                        "source_event_id": f"mercado_pago:{payment_id}",
                        "order_id": reference,
                    },
                ),
            )
        except Exception as conversion_exc:
            event_payload["conversion_trigger_error"] = conversion_exc.__class__.__name__
        await record_leoft_funnel_event(
            db,
            empresa_id=UUID(empresa_id),
            lead_id=UUID(lead_id),
            event_type="payment_confirmed",
            source="mercado_pago_webhook",
            anamnese_id=anamnese_id,
            checkout_reference=reference,
            payment_id=payment_id,
            metadata={"status": status, "amount": payment.get("transaction_amount"), "currency": payment.get("currency_id")},
            occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        from app.services.attribution_ledger import record_payment_touch

        approved_epoch = _payment_event_epoch(payment)
        await record_payment_touch(
            db,
            empresa_id=empresa_id,
            lead_id=lead_id,
            checkout_reference=reference,
            payment_id=payment_id,
            provider="mercado_pago",
            status="approved",
            occurred_at=datetime.fromtimestamp(approved_epoch, tz=timezone.utc),
            amount=payment.get("transaction_amount"),
            currency=str(payment.get("currency_id") or "").upper(),
        )
    elif status == "refunded":
        await record_leoft_funnel_event(
            db,
            empresa_id=UUID(empresa_id),
            lead_id=UUID(lead_id),
            event_type="payment_refunded",
            source="mercado_pago_webhook",
            anamnese_id=anamnese_id,
            checkout_reference=reference,
            payment_id=payment_id,
            metadata={"status": status, "amount": payment.get("transaction_amount"), "currency": payment.get("currency_id")},
            occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        from app.services.attribution_ledger import record_payment_touch

        refunded_epoch = _payment_event_epoch(payment)
        await record_payment_touch(
            db,
            empresa_id=empresa_id,
            lead_id=lead_id,
            checkout_reference=reference,
            payment_id=payment_id,
            provider="mercado_pago",
            status="refunded",
            occurred_at=datetime.fromtimestamp(refunded_epoch, tz=timezone.utc),
            amount=payment.get("transaction_amount"),
            currency=str(payment.get("currency_id") or "").upper(),
        )
    elif status in {"pending", "in_process"}:
        await record_leoft_funnel_event(
            db,
            empresa_id=UUID(empresa_id),
            lead_id=UUID(lead_id),
            event_type="payment_pending",
            source="mercado_pago_webhook",
            anamnese_id=anamnese_id,
            checkout_reference=reference,
            payment_id=payment_id,
            metadata={"status": status},
            occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    elif status in {"rejected", "cancelled", "canceled"}:
        await record_leoft_funnel_event(
            db,
            empresa_id=UUID(empresa_id),
            lead_id=UUID(lead_id),
            event_type="payment_failed_cancelled",
            source="mercado_pago_webhook",
            anamnese_id=anamnese_id,
            checkout_reference=reference,
            payment_id=payment_id,
            metadata={"status": status},
            occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    db.add(LeoFTIntegrationEvent(
        direction="inbound",
        integration="mercado_pago_leoft_payment",
        event_type=event_payload["event"],
        external_event_id=payment_id,
        idempotency_key=idempotency_key,
        request_payload_sanitized=event_payload,
        response_status=200,
        response_payload_sanitized={"ok": True, "payment_status": status},
        status="processed" if status == "approved" else "ignored",
        processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    await db.commit()
    return {"ok": True, "status": "processed", "payment_id": payment_id, "payment_status": status, "event": event_payload}


def _parse_meta_datetime_to_epoch(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _payment_event_epoch(payment: Mapping[str, Any]) -> int:
    for key in ("date_approved", "date_created", "date_last_updated"):
        parsed = _parse_meta_datetime_to_epoch(payment.get(key))
        if parsed is not None:
            return parsed
    raise LeoFTMercadoPagoWebhookError("payment_event_time_missing")
