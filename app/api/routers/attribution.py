from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.attribution_ledger import (
    campaign_attribution_summary,
    get_attribution_for_conversation,
    get_attribution_for_lead,
)
from db.database import get_db
from db.models import Usuario

router = APIRouter(
    prefix="/api/empresas/{empresa_id}/attribution",
    tags=["Attribution"],
    dependencies=[Depends(require_tenant_access)],
)


@router.get("/leads/{lead_id}")
async def attribution_for_lead(
    empresa_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    result = await get_attribution_for_lead(db, empresa_id=empresa_id, lead_id=lead_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return result


@router.get("/conversations/{conversation_id}")
async def attribution_for_conversation(
    empresa_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    result = await get_attribution_for_conversation(db, empresa_id=empresa_id, conversation_id=conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    return result


@router.get("/summary/campaigns")
async def attribution_campaign_summary(
    empresa_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await campaign_attribution_summary(db, empresa_id=empresa_id)
