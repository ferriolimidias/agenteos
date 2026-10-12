"""Super Admin-only, write-only secret configuration for synthetic JEV SHADOW."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_super_admin
from app.services import jev_config
from db.database import get_db


class SecretSafeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI normally echoes raw input, including secrets, in a 422 body.
                return JSONResponse(status_code=422, content={'detail': 'Configuração JEV inválida.'})
        return safe


router = APIRouter(prefix='/api/admin/empresas/{empresa_id}/integrations/jev', tags=['JEV Admin'],
    dependencies=[Depends(require_super_admin)], route_class=SecretSafeRoute)


async def checked_tenant(empresa_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        exists = await jev_config.tenant_exists(db, empresa_id)
    except SQLAlchemyError:
        raise HTTPException(503, 'Configuração JEV temporariamente indisponível.') from None
    if not exists:
        raise HTTPException(404, 'Empresa não encontrada.')
    return empresa_id


@router.get('')
async def get_config(empresa_id: UUID = Depends(checked_tenant), db: AsyncSession = Depends(get_db)):
    try:
        return (await jev_config.load_config(db, empresa_id, decrypt=False)).public()
    except SQLAlchemyError:
        raise HTTPException(503, 'Configuração JEV temporariamente indisponível.') from None


@router.put('')
async def update_config(update: jev_config.JevConfigUpdate, empresa_id: UUID = Depends(checked_tenant),
    db: AsyncSession = Depends(get_db)):
    try:
        return (await jev_config.save_config(db, empresa_id, update)).public()
    except jev_config.JevCredentialError:
        raise HTTPException(503, 'Armazenamento seguro de credenciais indisponível.') from None
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(503, 'Configuração JEV temporariamente indisponível.') from None


@router.post('/test')
async def connection_test(empresa_id: UUID = Depends(checked_tenant), db: AsyncSession = Depends(get_db)):
    try:
        return await jev_config.test_connection(db, empresa_id)
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(503, 'Configuração JEV temporariamente indisponível.') from None
