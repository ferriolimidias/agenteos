from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_super_admin
from app.services.meta.credential_crypto import MetaCredentialCryptoError
from app.services.payment_credentials import (
    get_payment_provider,
    resolve_mercado_pago_credentials,
    save_mercado_pago_credentials,
    serialize_mercado_pago_configuration,
    tenant_webhook_url,
)
from db.database import get_db
from db.models import Empresa, Usuario

router = APIRouter(
    prefix="/api/admin/empresas/{empresa_id}/payments/mercado-pago",
    tags=["Company payments"],
)


class MercadoPagoConfigurationUpdate(BaseModel):
    enabled: bool
    environment: str
    access_token: str | None = None
    webhook_secret: str | None = None


async def _empresa_or_404(db: AsyncSession, empresa_id: str) -> UUID:
    try:
        value = UUID(str(empresa_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ID da empresa inválido.")
    if await db.get(Empresa, value) is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return value


@router.get("")
async def get_mercado_pago_configuration(
    empresa_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    empresa_uuid = await _empresa_or_404(db, empresa_id)
    record = await get_payment_provider(db, empresa_id=empresa_uuid)
    return serialize_mercado_pago_configuration(
        record,
        webhook_url=tenant_webhook_url(empresa_id=empresa_uuid, request=request),
    )


@router.put("")
async def put_mercado_pago_configuration(
    empresa_id: str,
    payload: MercadoPagoConfigurationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    empresa_uuid = await _empresa_or_404(db, empresa_id)
    try:
        record = await save_mercado_pago_credentials(
            db,
            empresa_id=empresa_uuid,
            enabled=payload.enabled,
            environment=payload.environment,
            access_token=payload.access_token,
            webhook_secret=payload.webhook_secret,
        )
    except (ValueError, MetaCredentialCryptoError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_mercado_pago_configuration(
        record,
        webhook_url=tenant_webhook_url(empresa_id=empresa_uuid, request=request),
    )


@router.post("/test")
async def test_mercado_pago_connection(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    empresa_uuid = await _empresa_or_404(db, empresa_id)
    try:
        credentials = await resolve_mercado_pago_credentials(db, empresa_id=empresa_uuid)
    except MetaCredentialCryptoError:
        return {"valid": False, "message": "Credencial configurada inválida."}
    if not credentials.access_token:
        return {"valid": False, "message": "Access token não configurado."}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.get(
                "https://api.mercadopago.com/users/me",
                headers={"Authorization": f"Bearer {credentials.access_token}", "Accept": "application/json"},
            )
    except httpx.HTTPError:
        return {"valid": False, "message": "Mercado Pago indisponível para validação."}
    if response.status_code >= 400:
        return {"valid": False, "message": f"Credencial recusada pelo Mercado Pago ({response.status_code})."}
    return {"valid": True, "message": "Conexão validada."}
