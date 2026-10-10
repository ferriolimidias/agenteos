from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.mercado_pago.leoft_webhook import (
    LeoFTMercadoPagoWebhookError,
    extract_payment_id,
    lookup_payment,
    persist_payment_result,
    validate_signature,
)
from app.services.meta.credential_crypto import MetaCredentialCryptoError
from app.services.payment_credentials import resolve_mercado_pago_credentials
from db.database import get_db
from db.models import Empresa

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks/mercado-pago", tags=["LeoFT Mercado Pago"])


@router.post("/leoft")
async def receive_leoft_mercado_pago_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    credentials = await resolve_mercado_pago_credentials(None, empresa_id=None)
    return await _receive_webhook(request, db, credentials=credentials, expected_empresa_id=None)


@router.post("/{empresa_id}")
async def receive_company_mercado_pago_webhook(
    empresa_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        empresa_uuid = UUID(str(empresa_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="payment_tenant_not_found")
    if await db.get(Empresa, empresa_uuid) is None:
        raise HTTPException(status_code=404, detail="payment_tenant_not_found")
    try:
        credentials = await resolve_mercado_pago_credentials(db, empresa_id=empresa_uuid)
    except MetaCredentialCryptoError:
        raise HTTPException(status_code=503, detail="payment_credentials_unavailable")
    if credentials.source != "database" or not credentials.access_token or not credentials.webhook_secret:
        raise HTTPException(status_code=503, detail="payment_credentials_unavailable")
    return await _receive_webhook(
        request,
        db,
        credentials=credentials,
        expected_empresa_id=str(empresa_uuid),
    )


async def _receive_webhook(request: Request, db: AsyncSession, *, credentials, expected_empresa_id: str | None):
    raw_body = await request.body()
    if not validate_signature(
        raw_body=raw_body,
        x_signature=request.headers.get("x-signature"),
        x_request_id=request.headers.get("x-request-id"),
        secret=credentials.webhook_secret,
    ):
        raise HTTPException(status_code=401, detail="invalid_webhook_signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        if _is_production_simulation(payload, credentials=credentials):
            logger.info(
                "leoft.mercado_pago_webhook_simulation_received event_type=payment live_mode=false payment_id_present=%s",
                bool(((payload.get("data") or {}).get("id")) or payload.get("id")),
            )
            return {"ok": True, "status": "simulation_received", "event_type": "payment", "live_mode": False}
        payment_id = extract_payment_id(payload)
        payment = await lookup_payment(payment_id, access_token=credentials.access_token)
        return await persist_payment_result(db, payment, expected_empresa_id=expected_empresa_id)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid_webhook_json")
    except LeoFTMercadoPagoWebhookError as exc:
        logger.warning("leoft.mercado_pago_webhook_blocked reason=%s", str(exc)[:120])
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("leoft.mercado_pago_webhook_failed error_class=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="payment_processing_unavailable") from exc


def _is_production_simulation(payload: dict, *, credentials) -> bool:
    event_type = str(payload.get("type") or payload.get("topic") or "").strip().lower()
    live_mode = payload.get("live_mode")
    return (
        str(getattr(credentials, "environment", "") or "").strip().lower() == "production"
        and event_type == "payment"
        and (live_mode is False or str(live_mode).strip().lower() == "false")
    )
