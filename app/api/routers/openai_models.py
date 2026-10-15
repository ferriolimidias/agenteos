"""Super Admin controls for the selected tenant's OpenAI model catalog."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import Usuario, require_super_admin
from app.services.openai_model_catalog import CatalogUnavailable, list_models, validate_chat_model
from db.database import get_db

router = APIRouter(
    prefix="/api/admin/empresas/{empresa_id}/openai-models",
    tags=["OpenAI model catalog"],
    dependencies=[Depends(require_super_admin)],
)


class ValidateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=255)


@router.get("")
async def get_models(
    empresa_id: str,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    try:
        tenant_id = uuid.UUID(empresa_id)
        result = await list_models(db, tenant_id, refresh=refresh)
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        if "não configurou" in str(exc).lower():
            return {"items": [], "credential_status": "not_configured", "updated_at": None, "stale": False, "source": "tenant_openai_credential"}
        raise HTTPException(status_code=400, detail="Empresa inválida.") from None
    except CatalogUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail={"code": "catalog_refresh_failed", "provider_status": exc.code}) from None
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Empresa não encontrada ou catálogo indisponível.") from None


@router.post("/validate")
async def validate_model(
    empresa_id: str,
    payload: ValidateModelRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    try:
        tenant_id = uuid.UUID(empresa_id)
        result = await validate_chat_model(db, tenant_id, payload.model_id)
        await db.commit()
        return result
    except CatalogUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": "model_validation_failed", "provider_status": exc.code}) from None
    except ValueError as exc:
        await db.rollback()
        code = str(exc)
        status_code = 404 if code == "model_not_in_tenant_catalog" else 400
        raise HTTPException(status_code=status_code, detail={"code": code}) from None
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Empresa não encontrada ou validação indisponível.") from None
