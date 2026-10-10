"""HMAC-authenticated Hermes callbacks for LeoFT generation v1."""
# ruff: noqa: B008, BLE001

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.leoft_generation.errors import (
    AllowlistDeniedError,
    AttemptsExhaustedError,
    DomainValidationError,
    IdempotencyConflictError,
    InvalidJobTransitionError,
    InvalidTaskTransitionError,
    LeaseConflictError,
    OptimisticLockError,
    ResourceNotFoundError,
)
from app.schemas_leoft_generation_api import (
    AttemptPostRequest,
    AttemptResponse,
    BlueprintPutRequest,
    CheckpointPostRequest,
    CheckpointResponse,
    CompleteGenerationRequest,
    GenerationCompletionResponse,
    JobResponse,
    LeaseMutationRequest,
    LeaseRequest,
    SessionPutRequest,
    SessionResponse,
    SubmitReviewRequest,
    TaskFailureRequest,
    TaskInitializeRequest,
    TaskResponse,
)
from app.services import leoft_generation_api_service as service
from app.services.leoft_internal_auth import (
    HermesAuthContext,
    require_hermes_generation_hmac,
)
from db.database import get_db
from db.leoft_models import LeoFTGenerationCheckpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation/v1", tags=["LeoFT Generation v1"])


def _auth(scope: str, event: str):
    return Depends(require_hermes_generation_hmac(scope=scope, event=event))


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ResourceNotFoundError):
        return HTTPException(404, exc.code)
    if isinstance(exc, LeaseConflictError):
        return HTTPException(423, exc.code)
    if isinstance(
        exc,
        (
            IdempotencyConflictError,
            OptimisticLockError,
            InvalidJobTransitionError,
            InvalidTaskTransitionError,
        ),
    ):
        return HTTPException(409, exc.code)
    if isinstance(exc, AllowlistDeniedError):
        return HTTPException(403, exc.code)
    if isinstance(exc, (DomainValidationError, AttemptsExhaustedError)):
        return HTTPException(422, exc.code)
    if isinstance(exc, IntegrityError):
        return HTTPException(409, "persistence_conflict")
    return HTTPException(500, "generation_internal_error")


async def _rollback_raise(db: AsyncSession, exc: Exception) -> None:
    await db.rollback()
    raise _translate(exc)


async def _log_blueprint_failure(
    db: AsyncSession,
    *,
    job,
    body: BlueprintPutRequest,
    exc: Exception,
) -> None:
    checkpoint_version = await db.scalar(
        select(LeoFTGenerationCheckpoint.checkpoint_version)
        .where(
            LeoFTGenerationCheckpoint.company_id == job.company_id,
            LeoFTGenerationCheckpoint.generation_job_id == job.generation_job_id,
        )
        .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
        .limit(1)
    )
    logger.warning(
        "leoft blueprint callback rejected event_type=blueprint.put.v1 "
        "generation_job_id=%s correlation_id=%s domain_error_code=%s http=%s "
        "job_status=%s current_stage=%s expected_row_version=%s actual_row_version=%s "
        "expected_checkpoint_version=%s actual_checkpoint_version=%s "
        "blueprint_hash_declared=%s blueprint_hash_calculated=%s target_version_id=%s",
        job.generation_job_id,
        job.correlation_id,
        getattr(exc, "code", "generation_internal_error"),
        _translate(exc).status_code,
        job.status,
        job.current_stage,
        body.expected_row_version,
        job.row_version,
        body.expected_checkpoint_version,
        int(checkpoint_version or 0),
        body.blueprint_hash[:16],
        service.blueprint_hash_for_wire(body.blueprint)[:16],
        body.target_version_id,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    auth: HermesAuthContext = _auth(
        "leoft.generation.read", "leoft.generation.job.read.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        return await service.get_job_response(db, job)
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/jobs/{job_id}/blueprint", response_model=JobResponse)
async def put_blueprint(
    job_id: UUID,
    body: BlueprintPutRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.blueprint.write.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        result = await service.put_blueprint(
            db,
            job_id,
            body,
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        if "job" in locals():
            await _log_blueprint_failure(db, job=job, body=body, exc=exc)
        await _rollback_raise(db, exc)


@router.post("/jobs/{job_id}/tasks/initialize", response_model=list[TaskResponse])
async def initialize_tasks(
    job_id: UUID,
    body: TaskInitializeRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.tasks.initialize.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="tasks.initialize.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return [
                TaskResponse.model_validate(item) for item in repeated.get("items", [])
            ]
        result = await service.initialize_tasks(
            db, job_id, expected_row_version=body.expected_row_version
        )
        service.finish_idempotent_operation(
            event, {"items": [item.model_dump(mode="json") for item in result]}
        )
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.get("/jobs/{job_id}/tasks", response_model=list[TaskResponse])
async def list_tasks(
    job_id: UUID,
    auth: HermesAuthContext = _auth(
        "leoft.generation.read", "leoft.generation.tasks.read.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        return await service.list_tasks(db, job)
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/tasks/{task_id}/acquire", response_model=TaskResponse)
async def acquire_task(
    task_id: UUID,
    body: LeaseRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.task.acquire.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="task.acquire.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return TaskResponse.model_validate(repeated)
        result = await service.acquire_specific_task(
            db, task, lease_owner=body.lease_owner
        )
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post("/tasks/{task_id}/heartbeat", response_model=TaskResponse)
async def heartbeat_task(
    task_id: UUID,
    body: LeaseMutationRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.task.heartbeat.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="task.heartbeat.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return TaskResponse.model_validate(repeated)
        result = await service.heartbeat_task(
            db,
            task,
            owner=body.lease_owner,
            expected_row_version=body.expected_row_version,
        )
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post("/tasks/{task_id}/release", response_model=TaskResponse)
async def release_task(
    task_id: UUID,
    body: LeaseMutationRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.task.release.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="task.release.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return TaskResponse.model_validate(repeated)
        result = await service.release_task(
            db,
            task,
            owner=body.lease_owner,
            expected_row_version=body.expected_row_version,
        )
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post("/tasks/{task_id}/attempts", response_model=AttemptResponse)
async def append_attempt(
    task_id: UUID,
    body: AttemptPostRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.attempt.write.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> AttemptResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="attempt.write.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return AttemptResponse.model_validate(repeated)
        result = await service.append_attempt(
            db, task, job, body, idempotency_key=auth.idempotency_key
        )
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.put("/tasks/{task_id}/session", response_model=SessionResponse)
async def put_session(
    task_id: UUID,
    body: SessionPutRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.session.write.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="session.write.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return SessionResponse.model_validate(repeated)
        result = await service.put_session(db, task, job, body)
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post("/tasks/{task_id}/fail", response_model=TaskResponse)
async def fail_task(
    task_id: UUID,
    body: TaskFailureRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.task.fail.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    try:
        task, job = await service.find_task_with_job(db, task_id, lock=True)
        auth.require_company(job.company_id)
        if body.correlation_id != job.correlation_id:
            raise DomainValidationError("failure correlation mismatch")
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="task.fail.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return TaskResponse.model_validate(repeated)
        result = await service.fail_task(db, task, job, body)
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.get(
    "/jobs/{job_id}/checkpoints/latest", response_model=CheckpointResponse | None
)
async def latest_checkpoint(
    job_id: UUID,
    auth: HermesAuthContext = _auth(
        "leoft.generation.read", "leoft.generation.checkpoint.read.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> CheckpointResponse | None:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        return await service.latest_checkpoint(db, job)
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/checkpoints", response_model=CheckpointResponse)
async def create_checkpoint(
    job_id: UUID,
    body: CheckpointPostRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.checkpoint.write.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> CheckpointResponse:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="checkpoint.write.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return CheckpointResponse.model_validate(repeated)
        result = await service.create_checkpoint(db, job, body)
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post("/jobs/{job_id}/complete", response_model=GenerationCompletionResponse)
async def complete_generation(
    job_id: UUID,
    body: CompleteGenerationRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.complete.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> GenerationCompletionResponse:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        event, repeated = await service.begin_idempotent_operation(
            db,
            job=job,
            event_type="generation.complete.v1",
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        if repeated is not None:
            return GenerationCompletionResponse.model_validate(repeated)
        result = await service.complete_generation(db, job_id, body)
        service.finish_idempotent_operation(event, result.model_dump(mode="json"))
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)


@router.post(
    "/jobs/{job_id}/submit-review", response_model=GenerationCompletionResponse
)
async def submit_review(
    job_id: UUID,
    body: SubmitReviewRequest,
    auth: HermesAuthContext = _auth(
        "leoft.generation.write", "leoft.generation.review.submit.v1"
    ),
    db: AsyncSession = Depends(get_db),
) -> GenerationCompletionResponse:
    try:
        job = await service.find_job(db, job_id)
        auth.require_company(job.company_id)
        result, job = await service.submit_review(
            db,
            job_id,
            body,
            idempotency_key=auth.idempotency_key,
            request_hash=auth.request_hash,
        )
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await _rollback_raise(db, exc)
