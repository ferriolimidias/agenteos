"""Administrative operations for LeoFT generation recovery."""
# ruff: noqa: B008

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_super_admin
from app.leoft_generation.repository import LeoFTGenerationRepository
from app.leoft_generation.schemas import E2EAllowlist, Environment, GenerationPolicy
from app.leoft_generation.services import LeoFTGenerationService
from db.database import get_db

router = APIRouter(
    prefix="/api/admin/leoft/generation",
    tags=["LeoFT Generation Admin"],
    dependencies=[Depends(require_super_admin)],
)


class RedispatchRequest(BaseModel):
    company_id: UUID
    correlation_id: UUID
    reason: str = Field(min_length=8, max_length=255)


class RedispatchResponse(BaseModel):
    status: str
    generation_job_id: UUID
    redispatched: bool
    retryable: bool
    code: str | None = None
    response_status: int | None = None


@router.post("/jobs/{job_id}/redispatch", response_model=RedispatchResponse)
async def redispatch_generation_job(
    job_id: UUID,
    body: RedispatchRequest,
    db: AsyncSession = Depends(get_db),
) -> RedispatchResponse:
    repository = LeoFTGenerationRepository(db, company_id=body.company_id)
    service = LeoFTGenerationService(
        repository,
        policy=GenerationPolicy(
            environment=Environment.E2E,
            e2e_allowlist=E2EAllowlist(company_ids={body.company_id}),
        ),
    )
    job = await repository.get_job(job_id, lock=False)
    if job is None:
        raise HTTPException(404, "generation_job_not_found")
    if job.correlation_id != body.correlation_id:
        raise HTTPException(409, "generation_correlation_mismatch")
    result = await service.redispatch_generation_job_to_hermes(
        job_id=job_id,
        reason=body.reason,
    )
    await db.commit()
    return RedispatchResponse(
        status=result.status,
        generation_job_id=result.generation_job_id,
        redispatched=result.redispatched,
        retryable=result.retryable,
        code=result.code,
        response_status=result.response_status,
    )
