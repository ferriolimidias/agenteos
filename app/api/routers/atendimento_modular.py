"""Tenant-scoped pure simulation; no activation or commercial-action endpoints."""
from __future__ import annotations
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.routers.auth import require_tenant_access
from app.services import a07_preview
from app.services.a07_ledger import A07Ledger, LedgerUnavailable, new_client
from db.database import get_db
from db.models import Empresa

router = APIRouter(prefix='/api/empresas/{empresa_id}/atendimento-modular', tags=['Atendimento modular'], dependencies=[Depends(require_tenant_access)])


class PreviewPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    example_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r'^[a-z0-9_]+$')
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    context_mode: Literal['complete', 'incomplete', 'conflicting'] = 'incomplete'
    channel: Literal['whatsapp', 'instagram', 'simulator'] = 'whatsapp'
    use_jev: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validate_source(self):
        if bool(self.example_id) == bool(self.message) or self.message is not None and not self.message.strip():
            raise ValueError('Escolha um exemplo ou uma mensagem de simulação.')
        if self.use_jev and self.example_id not in a07_preview.A06_IDS:
            raise ValueError('JEV é permitido somente para exemplos A06 definidos pelo servidor.')
        return self


async def existing_tenant(empresa_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(Empresa.id).where(Empresa.id == empresa_id)) is None:
        raise HTTPException(status_code=404, detail='Empresa não encontrada.')
    return empresa_id


@router.get('/status')
async def status(empresa_id: uuid.UUID = Depends(existing_tenant), db: AsyncSession = Depends(get_db)):
    return await a07_preview.status(db, empresa_id)


@router.get('/examples')
async def examples(empresa_id: uuid.UUID = Depends(existing_tenant)):
    return {'schema_version': 'a07.v1', 'items': a07_preview.examples()}


@router.post('/preview')
async def preview(payload: PreviewPayload, empresa_id: uuid.UUID = Depends(existing_tenant), db: AsyncSession = Depends(get_db)):
    try:
        return await a07_preview.preview(db, empresa_id, **payload.model_dump())
    except a07_preview.ExampleNotFound as exc:
        raise HTTPException(status_code=404, detail='Exemplo não encontrado.') from exc


@router.get('/runs')
async def runs(empresa_id: uuid.UUID = Depends(existing_tenant), limit: int = Query(default=30, ge=1, le=100)):
    client = new_client()
    try:
        items = await A07Ledger(client, empresa_id).runs()
        return {'schema_version': 'a07.v1', 'items': items[:limit], 'total': len(items), 'limit': limit}
    except LedgerUnavailable as exc:
        raise HTTPException(status_code=503, detail='Trilha temporariamente indisponível.') from exc
    finally:
        await client.aclose()


@router.get('/runs/{run_id}')
async def run(run_id: uuid.UUID, empresa_id: uuid.UUID = Depends(existing_tenant)):
    client = new_client()
    try:
        record = await A07Ledger(client, empresa_id).get(str(run_id))
        if record is None:
            raise HTTPException(status_code=404, detail='Simulação não encontrada.')
        return record
    except LedgerUnavailable as exc:
        raise HTTPException(status_code=503, detail='Trilha temporariamente indisponível.') from exc
    finally:
        await client.aclose()
