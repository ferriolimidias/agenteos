"""Authenticated tenant-scoped Prompt Optimizer API. No message dispatch routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import Usuario, require_tenant_access
from app.services import a11_prompt_optimizer as service
from db.database import get_db
from db.models import is_admin_empresa_role, is_root_admin_email, is_super_admin_role

router = APIRouter(
    prefix="/api/empresas/{empresa_id}/prompt-optimizer",
    tags=["A11 Prompt Optimizer"],
    dependencies=[Depends(require_tenant_access)],
)


class CandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_key: str = Field(min_length=1, max_length=64)
    source_version_id: uuid.UUID
    content: str = Field(min_length=1, max_length=8000)
    change_summary: str = Field(min_length=1, max_length=1000)
    hypothesis: str = Field(min_length=1, max_length=1000)
    primary_metric: str = "response_after_7d"


class ExperimentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_key: str = Field(min_length=1, max_length=64)
    control_version_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_percent: int = Field(default=50, ge=1, le=99)
    sample_min_per_variant: int = Field(default=100, ge=20, le=10000)
    maturity_days: int = Field(default=7, ge=1, le=90)


class RollbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class EvaluationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_model: bool = False


class EvaluatorModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(default=None, max_length=255)


class EvaluatorModelValidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=255)


def _tenant_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Empresa inválida.") from None


def _actor_id(user: Usuario) -> str:
    return str(getattr(user, "id", None) or getattr(user, "email", "unknown"))


def _require_optimizer_admin(user: Usuario):
    if is_super_admin_role(user.role) or is_root_admin_email(user.email) or is_admin_empresa_role(user.role):
        return user
    raise HTTPException(status_code=403, detail="Apenas administradores podem alterar versões e experimentos.")


@router.get("")
async def get_optimizer_dashboard(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    return await service.dashboard(db, _tenant_uuid(empresa_id))


@router.get("/suggestions")
async def get_prompt_suggestions(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    return await service.evidence_suggestions(db, _tenant_uuid(empresa_id))


@router.post("/bootstrap")
async def bootstrap_reference_versions(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    result = await service.bootstrap_reference_versions(db, _tenant_uuid(empresa_id), actor_id=_actor_id(user))
    await db.commit()
    return result


@router.get("/evaluator/models")
async def get_evaluator_models(
    empresa_id: str,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        result = await service.evaluator_models(db, _tenant_uuid(empresa_id), refresh=refresh)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Catálogo OpenAI do tenant indisponível.") from None


@router.post("/evaluator/models/validate")
async def validate_evaluator_model(
    empresa_id: str,
    payload: EvaluatorModelValidationPayload,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    from app.services.openai_model_catalog import CatalogUnavailable, validate_chat_model
    try:
        result = await validate_chat_model(db, _tenant_uuid(empresa_id), payload.model_id)
        await db.commit()
        return result
    except (ValueError, CatalogUnavailable) as exc:
        await db.rollback()
        code = getattr(exc, "code", str(exc))
        raise HTTPException(status_code=422, detail={"code": code}) from None


@router.put("/evaluator")
async def set_evaluator_model(
    empresa_id: str,
    payload: EvaluatorModelPayload,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        result = await service.configure_evaluator(db, _tenant_uuid(empresa_id), payload.model_id, actor_id=_actor_id(user))
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from None


@router.post("/versions", status_code=201)
async def create_version(
    empresa_id: str,
    payload: CandidatePayload,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        row = await service.create_candidate(db, _tenant_uuid(empresa_id), **payload.model_dump(), actor_id=_actor_id(user))
        await db.commit()
        return {"id": str(row.id), "module_key": row.module_key, "version": row.version_number, "content_hash": row.content_hash, "status": "OFFLINE", "approved": False}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from None


@router.post("/versions/{version_id}/evaluate")
async def evaluate_version(
    empresa_id: str,
    version_id: str,
    payload: EvaluationPayload = Body(default=EvaluationPayload()),
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        result = await service.evaluate_version(db, _tenant_uuid(empresa_id), uuid.UUID(version_id), actor_id=_actor_id(user), use_model=payload.use_model)
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from None


@router.post("/versions/{version_id}/approve")
async def approve_version(
    empresa_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        result = await service.approve_version(db, _tenant_uuid(empresa_id), uuid.UUID(version_id), actor_id=_actor_id(user))
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from None


@router.post("/experiments", status_code=201)
async def create_experiment(
    empresa_id: str,
    payload: ExperimentPayload,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        values = payload.model_dump()
        values["sample_min"] = values.pop("sample_min_per_variant")
        row = await service.create_experiment(db, _tenant_uuid(empresa_id), **values, actor_id=_actor_id(user))
        await db.commit()
        return {"id": str(row.id), "status": row.status, "criteria": row.criteria_json, "message": "Rascunho criado; nenhuma exposição foi ativada."}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from None


@router.post("/experiments/{experiment_id}/start")
async def start_experiment(
    empresa_id: str,
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    try:
        row = await service.start_experiment(db, _tenant_uuid(empresa_id), uuid.UUID(experiment_id), actor_id=_actor_id(user))
        await db.commit()
        return {"id": str(row.id), "status": row.status, "live": service.live_status(row.empresa_id)}
    except ValueError as exc:
        await db.rollback()
        status_code = 409 if "flag" in str(exc) or "restart" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail={"code": str(exc)}) from None


@router.post("/experiments/{experiment_id}/decision")
async def evaluate_decision(
    empresa_id: str,
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    tenant_id = _tenant_uuid(empresa_id)
    try:
        experiment = await db.scalar(select(service.A11PromptExperiment).where(service.A11PromptExperiment.id == uuid.UUID(experiment_id), service.A11PromptExperiment.empresa_id == tenant_id))
        if experiment is None:
            raise ValueError("experiment_not_found")
        result = await service.promotion_decision(db, tenant_id, experiment, actor_id=_actor_id(user))
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from None


async def _finish_experiment(empresa_id: str, experiment_id: str, state: str, db: AsyncSession, user: Usuario):
    _require_optimizer_admin(user)
    try:
        row = await service.finish_experiment(db, _tenant_uuid(empresa_id), uuid.UUID(experiment_id), status=state, actor_id=_actor_id(user))
        await db.commit()
        return {"id": str(row.id), "status": row.status, "ends_at": row.ends_at.isoformat() if row.ends_at else None}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409 if str(exc) == "experiment_not_running" else 404, detail={"code": str(exc)}) from None


@router.post("/experiments/{experiment_id}/pause")
async def pause_experiment(empresa_id: str, experiment_id: str, db: AsyncSession = Depends(get_db), user: Usuario = Depends(require_tenant_access)):
    return await _finish_experiment(empresa_id, experiment_id, "PAUSED", db, user)


@router.post("/experiments/{experiment_id}/complete")
async def complete_experiment(empresa_id: str, experiment_id: str, db: AsyncSession = Depends(get_db), user: Usuario = Depends(require_tenant_access)):
    return await _finish_experiment(empresa_id, experiment_id, "COMPLETED", db, user)


@router.post("/experiments/{experiment_id}/rollback")
async def rollback_experiment(
    empresa_id: str,
    experiment_id: str,
    payload: RollbackPayload,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    _require_optimizer_admin(user)
    tenant_id = _tenant_uuid(empresa_id)
    try:
        experiment = await db.scalar(select(service.A11PromptExperiment).where(service.A11PromptExperiment.id == uuid.UUID(experiment_id), service.A11PromptExperiment.empresa_id == tenant_id))
        if experiment is None:
            raise ValueError("experiment_not_found")
        result = await service.rollback_experiment(db, tenant_id, experiment, reason=payload.reason, actor_id=_actor_id(user))
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from None
