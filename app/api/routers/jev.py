"""Explicit diagnostic requests only. No automatic hooks into active atendimento."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.routers.auth import require_super_admin, require_tenant_access
from app.services import jev_shadow
from app.services.jev_examples import examples
from app.services.jev_classification import INTENTS
from app.services.jev_ledger import JevLedger, LedgerUnavailable, new_client
from db.database import get_db

router = APIRouter(
    prefix='/api/empresas/{empresa_id}/jev',
    tags=['JEV SHADOW'],
    dependencies=[Depends(require_tenant_access), Depends(require_super_admin)],
)

class AnalyzePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    example_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r'^[a-z0-9_]+$')
    lead_id: uuid.UUID | None = None
    use_provider: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validate_source(self):
        if bool(self.example_id) == bool(self.lead_id):
            raise ValueError('Escolha exatamente example_id ou lead_id.')
        if self.use_provider and not self.example_id:
            raise ValueError('use_provider é opção somente de exemplos sintéticos controlados.')
        return self

@router.get('/status')
async def get_status(empresa_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await jev_shadow.status(db, empresa_id)

@router.get('/examples')
async def get_examples(empresa_id: uuid.UUID):
    return {'schema_version': 'a06.v1', 'items': examples(), 'reference_source': 'authored_synthetic_demo'}

@router.post('/analyze')
async def analyze(empresa_id: uuid.UUID, payload: AnalyzePayload, db: AsyncSession = Depends(get_db)):
    try:
        return await jev_shadow.analyze(db, empresa_id, **payload.model_dump())
    except jev_shadow.NotFound as exc:
        raise HTTPException(status_code=404, detail='Lead ou exemplo não encontrado.') from exc

@router.get('/runs')
async def get_runs(empresa_id: uuid.UUID, status: str | None = Query(default=None, max_length=32),
                   intent: str | None = Query(default=None, max_length=32), limit: int = Query(default=30, ge=1, le=100)):
    allowed_status = {'abstained','demo','completed','not_configured','privacy_blocked','not_evaluated','budget_exceeded','concurrency_limited','in_progress','provider_error','ledger_unavailable','disabled'}
    if status is not None and status not in allowed_status:
        raise HTTPException(status_code=422, detail='Status inválido.')
    if intent is not None and intent not in INTENTS:
        raise HTTPException(status_code=422, detail='Intenção inválida.')
    client = new_client()
    try:
        runs = await JevLedger(client, empresa_id).runs()
        def matches_status(run):
            if status == 'abstained':
                return run['status'] in ('demo', 'completed') and run['classification']['abstention']['abstained']
            return not status or run['status'] == status
        filtered = [r for r in runs if matches_status(r) and
                    (not intent or any(item['key'] == intent for item in r['classification']['intents']))]
        return {'schema_version': 'a06.v1', 'items': filtered[:limit], 'total': len(filtered), 'limit': limit}
    except LedgerUnavailable as exc:
        raise HTTPException(status_code=503, detail='Ledger JEV temporariamente indisponível.') from exc
    finally:
        await client.aclose()

@router.get('/runs/{run_id}')
async def get_run(empresa_id: uuid.UUID, run_id: uuid.UUID):
    client = new_client()
    try:
        run = await JevLedger(client, empresa_id).get(str(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail='Diagnóstico não encontrado.')
        return run
    except LedgerUnavailable as exc:
        raise HTTPException(status_code=503, detail='Ledger JEV temporariamente indisponível.') from exc
    finally:
        await client.aclose()
