"""Transactional operations behind the Hermes generation API.

This module flushes but never commits. The FastAPI route owns commit/rollback.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.leoft_generation.enums import (
    GenerationAttemptStatus,
    GenerationJobStatus,
    GenerationTaskStatus,
)
from app.leoft_generation.errors import (
    AttemptsExhaustedError,
    DomainValidationError,
    IdempotencyConflictError,
    InvalidJobTransitionError,
    InvalidTaskTransitionError,
    LeaseConflictError,
    OptimisticLockError,
    ResourceNotFoundError,
)
from app.leoft_generation.schemas import TrainingBlueprint
from app.leoft_generation.services import canonical_sha256, sanitize_pii, utc_now
from app.leoft_generation.state_machine import (
    validate_job_transition,
    validate_task_transition,
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
    SessionPutRequest,
    SessionResponse,
    SubmitReviewRequest,
    TaskFailureRequest,
    TaskResponse,
)
from db.leoft_models import (
    LeoFTAnamnesisSnapshot,
    LeoFTCatalogEntry,
    LeoFTCatalogMedia,
    LeoFTCatalogVersion,
    LeoFTGenerationAttempt,
    LeoFTGenerationCheckpoint,
    LeoFTGenerationJob,
    LeoFTGenerationSessionTask,
    LeoFTIntegrationEvent,
    LeoFTTrainingExercise,
    LeoFTTrainingPlanVersion,
    LeoFTTrainingSession,
    LeoFTTrainingWeek,
)

LEASE_SECONDS = 120


def _legacy_utc_now() -> datetime:
    """UTC for legacy columns declared as timestamp without time zone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_relative_storage_path(value: str | None) -> bool:
    if not value or value.startswith(("/", "\\")) or "://" in value:
        return False
    return ".." not in value.replace("\\", "/").split("/")


def _task_response(task: LeoFTGenerationSessionTask) -> TaskResponse:
    return TaskResponse.model_validate(task)


async def find_job(
    session: AsyncSession, job_id: UUID, *, lock: bool = False
) -> LeoFTGenerationJob:
    stmt = select(LeoFTGenerationJob).where(
        LeoFTGenerationJob.generation_job_id == job_id
    )
    if lock:
        stmt = stmt.with_for_update()
    job = await session.scalar(stmt)
    if job is None:
        raise ResourceNotFoundError("generation job not found")
    return job


async def find_task_with_job(
    session: AsyncSession, task_id: UUID, *, lock: bool = False
) -> tuple[LeoFTGenerationSessionTask, LeoFTGenerationJob]:
    stmt = (
        select(LeoFTGenerationSessionTask, LeoFTGenerationJob)
        .join(
            LeoFTGenerationJob,
            (
                LeoFTGenerationJob.generation_job_id
                == LeoFTGenerationSessionTask.generation_job_id
            )
            & (LeoFTGenerationJob.company_id == LeoFTGenerationSessionTask.company_id),
        )
        .where(LeoFTGenerationSessionTask.session_task_id == task_id)
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).first()
    if row is None:
        raise ResourceNotFoundError("generation task not found")
    return row[0], row[1]


async def get_job_response(
    session: AsyncSession, job: LeoFTGenerationJob
) -> JobResponse:
    snapshot = await session.scalar(
        select(LeoFTAnamnesisSnapshot).where(
            LeoFTAnamnesisSnapshot.company_id == job.company_id,
            LeoFTAnamnesisSnapshot.snapshot_id == job.anamnesis_snapshot_id,
        )
    )
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion).where(
            LeoFTTrainingPlanVersion.id == job.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
    )
    if snapshot is None or version is None:
        raise ResourceNotFoundError("generation inputs not found")
    tasks = list(
        (
            await session.scalars(
                select(LeoFTGenerationSessionTask).where(
                    LeoFTGenerationSessionTask.company_id == job.company_id,
                    LeoFTGenerationSessionTask.generation_job_id
                    == job.generation_job_id,
                )
            )
        ).all()
    )
    summary: dict[str, int] = {"total": len(tasks)}
    for task in tasks:
        summary[task.status] = summary.get(task.status, 0) + 1
    checkpoint = await session.scalar(
        select(LeoFTGenerationCheckpoint)
        .where(
            LeoFTGenerationCheckpoint.company_id == job.company_id,
            LeoFTGenerationCheckpoint.generation_job_id == job.generation_job_id,
        )
        .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
        .limit(1)
    )
    snapshot_payload = {
        "goal": snapshot.goal,
        "experience_level": snapshot.experience_level,
        "age": snapshot.age,
        "biological_data": snapshot.biological_data or {},
        "relevant_measurements": snapshot.relevant_measurements or {},
        "weekly_frequency": snapshot.weekly_frequency,
        "available_session_duration": snapshot.available_session_duration,
        "available_equipment": snapshot.available_equipment or [],
        "limitations": snapshot.limitations or [],
        "injuries": snapshot.injuries or [],
        "preferences": snapshot.preferences or {},
        "restrictions": snapshot.restrictions or [],
        "product_rules": snapshot.product_rules or {},
        "additional_answers": snapshot.additional_answers or {},
    }
    return JobResponse(
        generation_job_id=job.generation_job_id,
        company_id=job.company_id,
        product_id=job.product_id,
        status=job.status,
        current_stage=job.current_stage,
        row_version=job.row_version,
        target_version_id=job.target_version_id,
        snapshot_hash=snapshot.source_hash,
        snapshot=snapshot_payload,
        schema_version=version.schema_version,
        correlation_id=job.correlation_id,
        blueprint=dict(version.blueprint or {}),
        task_summary=summary,
        latest_checkpoint=(
            {
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "checkpoint_version": checkpoint.checkpoint_version,
                "stage": checkpoint.stage,
                "state_hash": checkpoint.state_hash,
            }
            if checkpoint
            else None
        ),
        limits={
            "requested_duration_weeks": job.requested_duration_weeks,
            "sessions_per_week": job.sessions_per_week,
            "maximum_attempts": job.maximum_attempts,
        },
    )


def _check_version(actual: int, expected: int) -> None:
    if int(actual or 0) != expected:
        raise OptimisticLockError()


def blueprint_hash_for_wire(blueprint: TrainingBlueprint) -> str:
    """Hash the same JSON document Hermes hashes before sending the callback."""
    return canonical_sha256(blueprint.model_dump(mode="json"))


def normalized_blueprint_payload(blueprint: TrainingBlueprint) -> dict[str, object]:
    """Fill deterministic session codes only in the persisted/task representation."""
    payload = blueprint.model_dump(mode="json")
    for item in payload["sessions"]:
        if item["session_code"] is None:
            item["session_code"] = (
                f"W{int(item['week_number']):02d}S{int(item['session_number']):02d}"
            )
    return payload


def _blueprint_hash_matches(
    version: LeoFTTrainingPlanVersion, blueprint_hash: str
) -> bool:
    return version.checksum == blueprint_hash


async def _idempotency_event(
    session: AsyncSession,
    *,
    company_id: UUID,
    plan_id: UUID | None,
    event_type: str,
    idempotency_key: str,
    request_hash: str,
) -> tuple[LeoFTIntegrationEvent, bool]:
    storage_key = f"{company_id}:{event_type}:{idempotency_key}"
    existing = await session.scalar(
        select(LeoFTIntegrationEvent)
        .where(
            LeoFTIntegrationEvent.integration == "hermes_generation_v1",
            LeoFTIntegrationEvent.idempotency_key == storage_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError()
        return existing, True
    event = LeoFTIntegrationEvent(
        direction="inbound",
        integration="hermes_generation_v1",
        event_type=event_type,
        external_event_id=None,
        idempotency_key=storage_key,
        plan_id=plan_id,
        request_hash=request_hash,
        request_payload_sanitized={},
        status="processing",
        attempt_count=1,
        created_at=_legacy_utc_now(),
        updated_at=_legacy_utc_now(),
    )
    session.add(event)
    await session.flush()
    return event, False


def _finish_event(event: LeoFTIntegrationEvent, payload: dict[str, Any]) -> None:
    safe_payload = dict(payload)
    safe_payload.pop("snapshot", None)
    event.status = "completed"
    event.response_status = 200
    event.response_payload_sanitized = sanitize_pii(safe_payload)
    event.processed_at = _legacy_utc_now()
    event.updated_at = _legacy_utc_now()


async def begin_idempotent_operation(
    session: AsyncSession,
    *,
    job: LeoFTGenerationJob,
    event_type: str,
    idempotency_key: str,
    request_hash: str,
) -> tuple[LeoFTIntegrationEvent, dict[str, Any] | None]:
    event, repeated = await _idempotency_event(
        session,
        company_id=job.company_id,
        plan_id=job.plan_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    return event, dict(event.response_payload_sanitized or {}) if repeated else None


def finish_idempotent_operation(
    event: LeoFTIntegrationEvent, payload: dict[str, Any]
) -> None:
    _finish_event(event, payload)


async def put_blueprint(
    session: AsyncSession,
    job_id: UUID,
    body: BlueprintPutRequest,
    *,
    idempotency_key: str,
    request_hash: str,
) -> JobResponse:
    job = await find_job(session, job_id, lock=True)
    event, repeated = await _idempotency_event(
        session,
        company_id=job.company_id,
        plan_id=job.plan_id,
        event_type="blueprint.put.v1",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion)
        .where(
            LeoFTTrainingPlanVersion.id == job.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
        .with_for_update()
    )
    if version is None:
        raise ResourceNotFoundError("target version not found")
    if body.blueprint_hash != blueprint_hash_for_wire(body.blueprint):
        raise DomainValidationError("blueprint_hash mismatch")
    if repeated:
        if not _blueprint_hash_matches(version, body.blueprint_hash):
            raise IdempotencyConflictError()
        return await get_job_response(session, job)
    _check_version(job.row_version, body.expected_row_version)
    if (
        body.target_version_id != job.target_version_id
        or body.correlation_id != job.correlation_id
    ):
        raise DomainValidationError("blueprint target/correlation mismatch")
    latest = await session.scalar(
        select(LeoFTGenerationCheckpoint)
        .where(
            LeoFTGenerationCheckpoint.company_id == job.company_id,
            LeoFTGenerationCheckpoint.generation_job_id == job.generation_job_id,
        )
        .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
        .limit(1)
    )
    if (
        int(latest.checkpoint_version if latest else 0)
        != body.expected_checkpoint_version
    ):
        raise OptimisticLockError("stale checkpoint version")
    task_exists = await session.scalar(
        select(LeoFTGenerationSessionTask.session_task_id)
        .where(
            LeoFTGenerationSessionTask.company_id == job.company_id,
            LeoFTGenerationSessionTask.generation_job_id == job.generation_job_id,
        )
        .limit(1)
    )
    if task_exists is not None:
        raise IdempotencyConflictError(
            "blueprint is immutable after task initialization"
        )
    if job.status == GenerationJobStatus.DISPATCHING.value:
        job.status = validate_job_transition(
            job.status, GenerationJobStatus.PLANNING
        ).value
    if job.status not in {
        GenerationJobStatus.PLANNING.value,
        GenerationJobStatus.GENERATING.value,
    }:
        raise InvalidJobTransitionError("blueprint requires planning/generating job")
    version.blueprint = normalized_blueprint_payload(body.blueprint)
    version.checksum = body.blueprint_hash
    version.status = "building"
    version.updated_at = utc_now()
    job.current_stage = "blueprint_persisted"
    job.model_provider = body.model_provider
    job.model_name = body.model_name
    job.input_tokens += body.input_tokens
    job.output_tokens += body.output_tokens
    job.estimated_cost = Decimal(job.estimated_cost or 0) + body.estimated_cost
    job.row_version += 1
    job.updated_at = utc_now()
    await session.flush()
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            stage="blueprint_persisted",
            blueprint_hash=body.blueprint_hash,
        ),
    )
    response = await get_job_response(session, job)
    _finish_event(event, response.model_dump(mode="json"))
    return response


async def initialize_tasks(
    session: AsyncSession, job_id: UUID, *, expected_row_version: int
) -> list[TaskResponse]:
    job = await find_job(session, job_id, lock=True)
    _check_version(job.row_version, expected_row_version)
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion)
        .where(
            LeoFTTrainingPlanVersion.id == job.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
        .with_for_update()
    )
    if version is None or not version.blueprint:
        raise DomainValidationError("blueprint is required")
    blueprint = TrainingBlueprint.model_validate(version.blueprint)
    existing = list(
        (
            await session.scalars(
                select(LeoFTGenerationSessionTask)
                .where(
                    LeoFTGenerationSessionTask.company_id == job.company_id,
                    LeoFTGenerationSessionTask.generation_job_id == job_id,
                )
                .order_by(LeoFTGenerationSessionTask.sequence)
            )
        ).all()
    )
    expected = [
        (item.session_code, item.objective, item.sequence)
        for item in blueprint.sessions
    ]
    if existing:
        actual = [
            (item.session_code, item.objective, item.sequence) for item in existing
        ]
        if actual != expected:
            raise IdempotencyConflictError("persisted tasks differ from blueprint")
        return [_task_response(item) for item in existing]
    if len(blueprint.sessions) != job.requested_duration_weeks * job.sessions_per_week:
        raise DomainValidationError("blueprint session count mismatch")
    expected_coordinates = {
        (week, session_number)
        for week in range(1, job.requested_duration_weeks + 1)
        for session_number in range(1, job.sessions_per_week + 1)
    }
    actual_coordinates = {
        (item.week_number, item.session_number) for item in blueprint.sessions
    }
    if actual_coordinates != expected_coordinates:
        raise DomainValidationError(
            "blueprint coordinates do not form the required grid"
        )
    now = utc_now()
    tasks = [
        LeoFTGenerationSessionTask(
            session_task_id=uuid.uuid4(),
            company_id=job.company_id,
            generation_job_id=job_id,
            target_version_id=job.target_version_id,
            phase_number=item.phase_number,
            week_number=item.week_number,
            session_number=item.session_number,
            session_code=item.session_code,
            objective=item.objective,
            sequence=item.sequence,
            status=GenerationTaskStatus.PENDING.value,
            maximum_attempts=job.maximum_attempts,
            created_at=now,
            updated_at=now,
        )
        for item in blueprint.sessions
    ]
    session.add_all(tasks)
    if job.status == GenerationJobStatus.PLANNING.value:
        job.status = validate_job_transition(
            job.status, GenerationJobStatus.GENERATING
        ).value
    job.current_stage = "tasks_initialized"
    job.row_version += 1
    job.updated_at = now
    await session.flush()
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            stage="tasks_initialized",
            blueprint_hash=str(version.checksum),
        ),
    )
    return [_task_response(item) for item in tasks]


async def list_tasks(
    session: AsyncSession, job: LeoFTGenerationJob
) -> list[TaskResponse]:
    rows = (
        await session.scalars(
            select(LeoFTGenerationSessionTask)
            .where(
                LeoFTGenerationSessionTask.company_id == job.company_id,
                LeoFTGenerationSessionTask.generation_job_id == job.generation_job_id,
            )
            .order_by(LeoFTGenerationSessionTask.sequence)
        )
    ).all()
    return [_task_response(item) for item in rows]


async def acquire_task(
    session: AsyncSession, job: LeoFTGenerationJob, *, lease_owner: str
) -> TaskResponse | None:
    now = utc_now()
    task = await session.scalar(
        select(LeoFTGenerationSessionTask)
        .where(
            LeoFTGenerationSessionTask.company_id == job.company_id,
            LeoFTGenerationSessionTask.generation_job_id == job.generation_job_id,
            LeoFTGenerationSessionTask.status.in_(
                ["pending", "generating", "validating", "repairing"]
            ),
            (LeoFTGenerationSessionTask.lease_owner.is_(None))
            | (LeoFTGenerationSessionTask.lease_expires_at.is_(None))
            | (LeoFTGenerationSessionTask.lease_expires_at <= now),
        )
        .order_by(LeoFTGenerationSessionTask.sequence)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if task is None:
        return None
    if task.status == GenerationTaskStatus.PENDING.value:
        task.status = validate_task_transition(
            task.status, GenerationTaskStatus.GENERATING
        ).value
        task.started_at = task.started_at or now
    task.lease_owner = lease_owner
    task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    task.row_version += 1
    task.updated_at = now
    await session.flush()
    return _task_response(task)


async def acquire_specific_task(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    *,
    lease_owner: str,
) -> TaskResponse:
    now = utc_now()
    if task.status in {"completed", "failed", "cancelled"}:
        raise LeaseConflictError("terminal task cannot be acquired")
    if (
        task.lease_owner
        and task.lease_owner != lease_owner
        and task.lease_expires_at
        and task.lease_expires_at > now
    ):
        raise LeaseConflictError()
    if task.status == GenerationTaskStatus.PENDING.value:
        task.status = validate_task_transition(
            task.status, GenerationTaskStatus.GENERATING
        ).value
        task.started_at = task.started_at or now
    task.lease_owner = lease_owner
    task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    task.row_version += 1
    task.updated_at = now
    await session.flush()
    return _task_response(task)


def _require_lease(task: LeoFTGenerationSessionTask, owner: str) -> None:
    now = utc_now()
    if (
        task.lease_owner != owner
        or task.lease_expires_at is None
        or task.lease_expires_at <= now
    ):
        raise LeaseConflictError()


async def heartbeat_task(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    *,
    owner: str,
    expected_row_version: int,
) -> TaskResponse:
    _check_version(task.row_version, expected_row_version)
    _require_lease(task, owner)
    task.lease_expires_at = utc_now() + timedelta(seconds=LEASE_SECONDS)
    task.row_version += 1
    task.updated_at = utc_now()
    await session.flush()
    return _task_response(task)


async def release_task(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    *,
    owner: str,
    expected_row_version: int,
) -> TaskResponse:
    _check_version(task.row_version, expected_row_version)
    if task.lease_owner != owner:
        raise LeaseConflictError()
    task.lease_owner = None
    task.lease_expires_at = None
    task.row_version += 1
    task.updated_at = utc_now()
    await session.flush()
    return _task_response(task)


async def append_attempt(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    job: LeoFTGenerationJob,
    body: AttemptPostRequest,
    *,
    idempotency_key: str,
) -> AttemptResponse:
    existing = await session.scalar(
        select(LeoFTGenerationAttempt).where(
            LeoFTGenerationAttempt.company_id == job.company_id,
            LeoFTGenerationAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.session_task_id != task.session_task_id
            or existing.request_hash != body.request_hash
            or existing.response_hash != body.response_hash
        ):
            raise IdempotencyConflictError()
        return AttemptResponse(
            attempt_id=existing.attempt_id,
            attempt_number=existing.attempt_number,
            status=existing.status,
            task_row_version=task.row_version,
        )
    _check_version(task.row_version, body.expected_task_row_version)
    if body.correlation_id != job.correlation_id:
        raise DomainValidationError("attempt correlation mismatch")
    number = int(task.attempt_count or 0) + 1
    if number > int(task.maximum_attempts):
        raise AttemptsExhaustedError()
    attempt = LeoFTGenerationAttempt(
        attempt_id=uuid.uuid4(),
        company_id=job.company_id,
        generation_job_id=job.generation_job_id,
        session_task_id=task.session_task_id,
        target_version_id=task.target_version_id,
        attempt_number=number,
        attempt_type=body.attempt_type,
        stage=body.stage,
        prompt_version=body.prompt_version,
        catalog_version=body.catalog_version,
        status=body.status.value,
        provider=body.provider,
        model=body.model,
        model_version=body.model_version,
        provider_request_id=body.provider_request_id,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        total_tokens=body.input_tokens + body.output_tokens,
        estimated_cost=body.estimated_cost,
        currency=body.currency,
        latency_ms=body.latency_ms,
        request_hash=body.request_hash,
        response_hash=body.response_hash,
        validation_result=sanitize_pii(body.validation_result),
        errors=sanitize_pii(body.errors),
        repair_instructions=sanitize_pii(body.repair_instructions),
        error_code=body.error_code,
        error_summary=sanitize_pii(body.error_summary) if body.error_summary else None,
        started_at=body.started_at,
        completed_at=body.finished_at
        if body.status == GenerationAttemptStatus.SUCCEEDED
        else None,
        failed_at=body.finished_at
        if body.status
        not in {GenerationAttemptStatus.STARTED, GenerationAttemptStatus.SUCCEEDED}
        else None,
        created_at=utc_now(),
        correlation_id=job.correlation_id,
        idempotency_key=idempotency_key,
    )
    session.add(attempt)
    task.attempt_count = number
    task.selected_provider = body.provider
    task.selected_model = body.model
    task.input_tokens += body.input_tokens
    task.output_tokens += body.output_tokens
    task.estimated_cost = Decimal(task.estimated_cost or 0) + body.estimated_cost
    task.row_version += 1
    task.updated_at = utc_now()
    job.input_tokens += body.input_tokens
    job.output_tokens += body.output_tokens
    job.estimated_cost = Decimal(job.estimated_cost or 0) + body.estimated_cost
    if (
        body.status == GenerationAttemptStatus.SUCCEEDED
        and task.status == GenerationTaskStatus.GENERATING.value
    ):
        task.status = validate_task_transition(
            task.status, GenerationTaskStatus.VALIDATING
        ).value
    elif (
        body.status == GenerationAttemptStatus.VALIDATION_FAILED
        and task.status == GenerationTaskStatus.VALIDATING.value
    ):
        task.status = validate_task_transition(
            task.status, GenerationTaskStatus.REPAIRING
        ).value
    await session.flush()
    return AttemptResponse(
        attempt_id=attempt.attempt_id,
        attempt_number=number,
        status=attempt.status,
        task_row_version=task.row_version,
    )


async def put_session(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    job: LeoFTGenerationJob,
    body: SessionPutRequest,
) -> SessionResponse:
    _check_version(task.row_version, body.expected_row_version)
    _require_lease(task, body.lease_owner)
    if (
        body.target_version_id != task.target_version_id
        or body.week_number != task.week_number
        or body.session_number != task.session_number
        or body.session_code != task.session_code
        or body.sequence != task.sequence
        or body.correlation_id != job.correlation_id
    ):
        raise DomainValidationError("session coordinates/correlation mismatch")
    if task.status != GenerationTaskStatus.VALIDATING.value:
        raise InvalidTaskTransitionError("session persistence requires validating task")
    if body.validation.get("status") not in {"valid", "completed", "passed"}:
        raise DomainValidationError("session validation must be successful")
    payload = body.model_dump(
        mode="json",
        exclude={"expected_row_version", "lease_owner", "content_hash"},
    )
    content_hash = canonical_sha256(payload)
    if content_hash != body.content_hash:
        raise DomainValidationError("session content_hash mismatch")
    attempt = await session.scalar(
        select(LeoFTGenerationAttempt).where(
            LeoFTGenerationAttempt.company_id == job.company_id,
            LeoFTGenerationAttempt.attempt_id == body.attempt_id,
            LeoFTGenerationAttempt.session_task_id == task.session_task_id,
            LeoFTGenerationAttempt.status == GenerationAttemptStatus.SUCCEEDED.value,
        )
    )
    if attempt is None:
        raise DomainValidationError("successful attempt is required")
    existing = await session.scalar(
        select(LeoFTTrainingSession)
        .where(
            LeoFTTrainingSession.version_id == task.target_version_id,
            LeoFTTrainingSession.session_task_id == task.session_task_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (existing.source_json or {}).get("content_hash") != content_hash:
            raise IdempotencyConflictError("session content differs")
        count = len((existing.source_json or {}).get("exercise_ids", []))
        return SessionResponse(
            session_id=existing.id,
            session_task_id=task.session_task_id,
            content_hash=content_hash,
            task_row_version=task.row_version,
            exercise_count=count,
        )
    catalog_version = await session.scalar(
        select(LeoFTCatalogVersion).where(
            LeoFTCatalogVersion.version == body.exercises[0].catalog_version,
            LeoFTCatalogVersion.status == "imported",
        )
    )
    if catalog_version is None or any(
        item.catalog_version != catalog_version.version for item in body.exercises
    ):
        raise DomainValidationError("catalog version is not active or consistent")
    resolved: list[tuple[Any, LeoFTCatalogEntry, LeoFTCatalogMedia]] = []
    for exercise in body.exercises:
        entry = await session.scalar(
            select(LeoFTCatalogEntry).where(
                LeoFTCatalogEntry.catalog_id == exercise.catalog_id,
                LeoFTCatalogEntry.approval_status.notin_(["rejected", "duplicate"]),
            )
        )
        media = await session.scalar(
            select(LeoFTCatalogMedia).where(
                LeoFTCatalogMedia.media_id == exercise.media_id
            )
        )
        if (
            entry is None
            or media is None
            or media.catalog_entry_id != entry.id
            or not _safe_relative_storage_path(media.storage_path)
        ):
            raise DomainValidationError("catalog/media association is invalid")
        for substitute in exercise.substitutions:
            sub_entry = await session.scalar(
                select(LeoFTCatalogEntry).where(
                    LeoFTCatalogEntry.catalog_id == substitute["catalog_id"]
                )
            )
            sub_media = await session.scalar(
                select(LeoFTCatalogMedia).where(
                    LeoFTCatalogMedia.media_id == substitute["media_id"]
                )
            )
            if (
                sub_entry is None
                or sub_entry.approval_status in {"rejected", "duplicate"}
                or sub_media is None
                or sub_media.catalog_entry_id != sub_entry.id
                or not _safe_relative_storage_path(sub_media.storage_path)
            ):
                raise DomainValidationError("substitution association is invalid")
        resolved.append((exercise, entry, media))
    week = await session.scalar(
        select(LeoFTTrainingWeek)
        .where(
            LeoFTTrainingWeek.version_id == task.target_version_id,
            LeoFTTrainingWeek.week_number == task.week_number,
        )
        .with_for_update()
    )
    now = utc_now()
    legacy_now = _legacy_utc_now()
    if week is None:
        week = LeoFTTrainingWeek(
            id=uuid.uuid4(),
            plan_id=job.plan_id,
            version_id=task.target_version_id,
            week_number=task.week_number,
            phase_number=task.phase_number,
            objective=body.week_objective,
            progression_notes=body.progression_notes,
            status="generating",
            position=task.week_number - 1,
            source_json={"schema_version": "leoft.training-plan.v2"},
            created_at=legacy_now,
            updated_at=legacy_now,
        )
        session.add(week)
        await session.flush()
    training_session = LeoFTTrainingSession(
        id=uuid.uuid4(),
        plan_id=job.plan_id,
        version_id=task.target_version_id,
        week_id=week.id,
        external_session_id=task.session_code,
        session_task_id=task.session_task_id,
        session_code=task.session_code,
        objective=body.objective,
        sequence=task.sequence,
        estimated_duration_minutes=body.estimated_duration_minutes,
        generation_status="completed",
        position=task.session_number - 1,
        day_label=body.day_label,
        title=body.title,
        session_type=body.session_type,
        warmup=body.warmup,
        cooldown=body.cooldown,
        estimated_duration=f"{body.estimated_duration_minutes} min",
        notes=sanitize_pii(body.notes),
        source_json={
            "schema_version": "leoft.training-plan.v2",
            "content_hash": content_hash,
            "validation": sanitize_pii(body.validation),
            "exercise_ids": [item.external_exercise_id for item in body.exercises],
        },
        created_at=legacy_now,
        updated_at=legacy_now,
    )
    session.add(training_session)
    await session.flush()
    for exercise, entry, _media in resolved:
        session.add(
            LeoFTTrainingExercise(
                id=uuid.uuid4(),
                plan_id=job.plan_id,
                version_id=task.target_version_id,
                session_id=training_session.id,
                external_exercise_id=exercise.external_exercise_id,
                position=exercise.order - 1,
                catalog_id=exercise.catalog_id,
                media_id=exercise.media_id,
                catalog_version=exercise.catalog_version,
                exercise_order=exercise.order,
                name=entry.canonical_name,
                muscle_group=entry.muscle_group,
                sets=exercise.sets,
                reps=exercise.repetitions,
                repetitions=exercise.repetitions,
                duration_seconds=exercise.duration_seconds,
                rest_seconds=exercise.rest_seconds,
                intensity=exercise.intensity,
                tempo=exercise.tempo,
                load_guidance=exercise.load_guidance,
                exercise_notes=exercise.notes,
                progression_rule=sanitize_pii(exercise.progression_rule),
                substitutions=sanitize_pii(exercise.substitutions),
                prescription_mode=exercise.prescription_mode,
                canonical_name=entry.canonical_name,
                selection_reason=exercise.selection_reason,
                association_validated_at=now,
                source_json={"schema_version": "leoft.training-plan.v2"},
                created_at=legacy_now,
                updated_at=legacy_now,
            )
        )
    await session.flush()
    task.status = validate_task_transition(
        task.status, GenerationTaskStatus.COMPLETED
    ).value
    task.completed_at = now
    task.lease_owner = None
    task.lease_expires_at = None
    task.row_version += 1
    task.updated_at = now
    await session.flush()
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion).where(
            LeoFTTrainingPlanVersion.id == task.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
    )
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            current_session_task_id=task.session_task_id,
            stage=f"session:{task.session_code}:completed",
            blueprint_hash=str(version.checksum),
            last_attempt_id=body.attempt_id,
        ),
    )
    return SessionResponse(
        session_id=training_session.id,
        session_task_id=task.session_task_id,
        content_hash=content_hash,
        task_row_version=task.row_version,
        exercise_count=len(body.exercises),
    )


async def fail_task(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    job: LeoFTGenerationJob,
    body: TaskFailureRequest,
) -> TaskResponse:
    _check_version(task.row_version, body.expected_row_version)
    _require_lease(task, body.lease_owner)
    attempt = await session.scalar(
        select(LeoFTGenerationAttempt).where(
            LeoFTGenerationAttempt.company_id == task.company_id,
            LeoFTGenerationAttempt.attempt_id == body.attempt_id,
            LeoFTGenerationAttempt.session_task_id == task.session_task_id,
            LeoFTGenerationAttempt.correlation_id == body.correlation_id,
        )
    )
    if attempt is None:
        raise DomainValidationError("failure attempt/correlation mismatch")
    target = (
        GenerationTaskStatus.PENDING
        if body.retryable and task.attempt_count < task.maximum_attempts
        else GenerationTaskStatus.FAILED
    )
    task.status = validate_task_transition(task.status, target).value
    task.last_error_code = body.error_code
    task.last_error_summary = sanitize_pii(body.error_summary)
    task.failed_at = utc_now() if target == GenerationTaskStatus.FAILED else None
    task.lease_owner = None
    task.lease_expires_at = None
    task.row_version += 1
    task.updated_at = utc_now()
    await session.flush()
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion).where(
            LeoFTTrainingPlanVersion.id == task.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
    )
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            current_session_task_id=task.session_task_id,
            stage=f"session:{task.session_code}:{task.status}",
            blueprint_hash=str(version.checksum),
            last_attempt_id=body.attempt_id,
        ),
    )
    return _task_response(task)


async def retry_task(
    session: AsyncSession,
    task: LeoFTGenerationSessionTask,
    *,
    expected_row_version: int,
) -> TaskResponse:
    _check_version(task.row_version, expected_row_version)
    if task.status != GenerationTaskStatus.REPAIRING.value:
        raise InvalidTaskTransitionError("retry only applies to repairing tasks")
    if task.attempt_count >= task.maximum_attempts:
        raise AttemptsExhaustedError()
    task.status = validate_task_transition(
        task.status, GenerationTaskStatus.GENERATING
    ).value
    task.row_version += 1
    task.updated_at = utc_now()
    await session.flush()
    return _task_response(task)


async def complete_task(
    session: AsyncSession, task: LeoFTGenerationSessionTask
) -> TaskResponse:
    if task.status == GenerationTaskStatus.COMPLETED.value:
        return _task_response(task)
    persisted = await session.scalar(
        select(LeoFTTrainingSession.id).where(
            LeoFTTrainingSession.version_id == task.target_version_id,
            LeoFTTrainingSession.session_task_id == task.session_task_id,
            LeoFTTrainingSession.generation_status == "completed",
        )
    )
    if persisted is None:
        raise DomainValidationError("validated session is required")
    task.status = validate_task_transition(
        task.status, GenerationTaskStatus.COMPLETED
    ).value
    task.completed_at = utc_now()
    task.lease_owner = None
    task.lease_expires_at = None
    task.row_version += 1
    task.updated_at = utc_now()
    await session.flush()
    return _task_response(task)


async def latest_checkpoint(
    session: AsyncSession, job: LeoFTGenerationJob
) -> CheckpointResponse | None:
    item = await session.scalar(
        select(LeoFTGenerationCheckpoint)
        .where(
            LeoFTGenerationCheckpoint.company_id == job.company_id,
            LeoFTGenerationCheckpoint.generation_job_id == job.generation_job_id,
        )
        .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
        .limit(1)
    )
    if item is None:
        return None
    return CheckpointResponse.model_validate(item)


async def create_checkpoint(
    session: AsyncSession, job: LeoFTGenerationJob, body: CheckpointPostRequest
) -> CheckpointResponse:
    locked = await find_job(session, job.generation_job_id, lock=True)
    latest = await session.scalar(
        select(LeoFTGenerationCheckpoint)
        .where(
            LeoFTGenerationCheckpoint.company_id == locked.company_id,
            LeoFTGenerationCheckpoint.generation_job_id == locked.generation_job_id,
        )
        .order_by(LeoFTGenerationCheckpoint.checkpoint_version.desc())
        .limit(1)
        .with_for_update()
    )
    tasks = list(
        (
            await session.scalars(
                select(LeoFTGenerationSessionTask)
                .where(
                    LeoFTGenerationSessionTask.company_id == locked.company_id,
                    LeoFTGenerationSessionTask.generation_job_id
                    == locked.generation_job_id,
                )
                .order_by(LeoFTGenerationSessionTask.sequence)
            )
        ).all()
    )
    task_ids = {item.session_task_id for item in tasks}
    if (
        body.current_session_task_id is not None
        and body.current_session_task_id not in task_ids
    ):
        raise DomainValidationError("checkpoint task does not belong to job")
    if body.last_attempt_id is not None:
        attempt_belongs = await session.scalar(
            select(LeoFTGenerationAttempt.attempt_id).where(
                LeoFTGenerationAttempt.company_id == locked.company_id,
                LeoFTGenerationAttempt.generation_job_id == locked.generation_job_id,
                LeoFTGenerationAttempt.attempt_id == body.last_attempt_id,
            )
        )
        if attempt_belongs is None:
            raise DomainValidationError("checkpoint attempt does not belong to job")
    snapshot = await session.scalar(
        select(LeoFTAnamnesisSnapshot).where(
            LeoFTAnamnesisSnapshot.company_id == locked.company_id,
            LeoFTAnamnesisSnapshot.snapshot_id == locked.anamnesis_snapshot_id,
        )
    )
    if snapshot is None:
        raise ResourceNotFoundError("snapshot not found")
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion).where(
            LeoFTTrainingPlanVersion.id == locked.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == locked.plan_id,
        )
    )
    if version is None or not version.blueprint:
        raise ResourceNotFoundError("checkpoint blueprint not found")
    if not _blueprint_hash_matches(version, body.blueprint_hash):
        raise DomainValidationError("checkpoint blueprint_hash mismatch")
    blueprint_hash = body.blueprint_hash
    completed = [
        str(item.session_task_id) for item in tasks if item.status == "completed"
    ]
    failed = [str(item.session_task_id) for item in tasks if item.status == "failed"]
    pending = [
        str(item.session_task_id)
        for item in tasks
        if item.status not in {"completed", "failed"}
    ]
    stage = body.stage or locked.current_stage or locked.status
    state = {
        "job_status": locked.status,
        "stage": stage,
        "current_session_task_id": body.current_session_task_id,
        "completed_task_ids": completed,
        "pending_task_ids": pending,
        "failed_task_ids": failed,
        "blueprint_hash": blueprint_hash,
        "snapshot_hash": snapshot.source_hash,
        "last_attempt_id": body.last_attempt_id,
    }
    item = LeoFTGenerationCheckpoint(
        checkpoint_id=uuid.uuid4(),
        company_id=locked.company_id,
        generation_job_id=locked.generation_job_id,
        checkpoint_version=int(latest.checkpoint_version if latest else 0) + 1,
        target_version_id=locked.target_version_id,
        job_status=locked.status,
        stage=stage,
        current_session_task_id=body.current_session_task_id,
        completed_task_ids=completed,
        pending_task_ids=pending,
        failed_task_ids=failed,
        blueprint_hash=blueprint_hash,
        snapshot_hash=snapshot.source_hash,
        state_hash=canonical_sha256(state),
        last_attempt_id=body.last_attempt_id,
        captured_at=utc_now(),
        correlation_id=locked.correlation_id,
        created_at=utc_now(),
    )
    session.add(item)
    await session.flush()
    locked.latest_checkpoint_id = item.checkpoint_id
    locked.row_version += 1
    for task in tasks:
        task.checkpoint_version = item.checkpoint_version
    await session.flush()
    return CheckpointResponse.model_validate(item)


async def complete_generation(
    session: AsyncSession, job_id: UUID, body: CompleteGenerationRequest
) -> GenerationCompletionResponse:
    job = await find_job(session, job_id, lock=True)
    _check_version(job.row_version, body.expected_row_version)
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion)
        .where(
            LeoFTTrainingPlanVersion.id == job.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
        .with_for_update()
    )
    if version is None or not _blueprint_hash_matches(
        version, body.expected_blueprint_hash
    ):
        raise DomainValidationError("blueprint missing or hash mismatch")
    if body.correlation_id != job.correlation_id:
        raise DomainValidationError("completion correlation mismatch")
    tasks = list(
        (
            await session.scalars(
                select(LeoFTGenerationSessionTask)
                .where(
                    LeoFTGenerationSessionTask.company_id == job.company_id,
                    LeoFTGenerationSessionTask.generation_job_id == job_id,
                )
                .order_by(LeoFTGenerationSessionTask.sequence)
            )
        ).all()
    )
    expected_count = job.requested_duration_weeks * job.sessions_per_week
    if len(tasks) != expected_count or any(
        item.status != "completed" for item in tasks
    ):
        raise DomainValidationError("all expected tasks must be completed")
    rows = (
        await session.execute(
            select(LeoFTTrainingWeek, LeoFTTrainingSession, LeoFTTrainingExercise)
            .join(
                LeoFTTrainingSession,
                LeoFTTrainingSession.week_id == LeoFTTrainingWeek.id,
            )
            .join(
                LeoFTTrainingExercise,
                LeoFTTrainingExercise.session_id == LeoFTTrainingSession.id,
            )
            .where(LeoFTTrainingWeek.version_id == job.target_version_id)
            .order_by(
                LeoFTTrainingWeek.week_number,
                LeoFTTrainingSession.sequence,
                LeoFTTrainingExercise.exercise_order,
            )
        )
    ).all()
    if not rows:
        raise DomainValidationError("version content is empty")
    distinct_weeks = {week.id for week, _training_session, _exercise in rows}
    distinct_sessions = {
        training_session.id for _week, training_session, _exercise in rows
    }
    linked_tasks = {
        training_session.session_task_id for _week, training_session, _exercise in rows
    }
    if (
        len(distinct_weeks) != job.requested_duration_weeks
        or len(distinct_sessions) != expected_count
        or linked_tasks != {task.session_task_id for task in tasks}
    ):
        raise DomainValidationError("persisted week/session grid mismatch")
    active_catalog = await session.scalar(
        select(LeoFTCatalogVersion).where(
            LeoFTCatalogVersion.version == rows[0][2].catalog_version,
            LeoFTCatalogVersion.status == "imported",
        )
    )
    if active_catalog is None:
        raise DomainValidationError("catalog version is not imported")
    for _week, _training_session, exercise in rows:
        entry = await session.scalar(
            select(LeoFTCatalogEntry).where(
                LeoFTCatalogEntry.catalog_id == exercise.catalog_id,
                LeoFTCatalogEntry.approval_status.notin_(["rejected", "duplicate"]),
            )
        )
        media = await session.scalar(
            select(LeoFTCatalogMedia).where(
                LeoFTCatalogMedia.media_id == exercise.media_id,
            )
        )
        if (
            entry is None
            or media is None
            or media.catalog_entry_id != entry.id
            or exercise.catalog_version != active_catalog.version
            or not _safe_relative_storage_path(media.storage_path)
        ):
            raise DomainValidationError(
                "catalog/media association changed before completion"
            )
        for substitute in exercise.substitutions or []:
            sub_entry = await session.scalar(
                select(LeoFTCatalogEntry).where(
                    LeoFTCatalogEntry.catalog_id == substitute.get("catalog_id"),
                    LeoFTCatalogEntry.approval_status.notin_(["rejected", "duplicate"]),
                )
            )
            sub_media = await session.scalar(
                select(LeoFTCatalogMedia).where(
                    LeoFTCatalogMedia.media_id == substitute.get("media_id"),
                )
            )
            if (
                sub_entry is None
                or sub_media is None
                or sub_media.catalog_entry_id != sub_entry.id
                or not _safe_relative_storage_path(sub_media.storage_path)
            ):
                raise DomainValidationError(
                    "substitution association changed before completion"
                )
    content_hash = canonical_sha256(
        {
            "blueprint": version.blueprint,
            "content": [
                {
                    "week": week.week_number,
                    "phase": week.phase_number,
                    "session": training_session.session_code,
                    "exercise": exercise.external_exercise_id,
                    "catalog_id": exercise.catalog_id,
                    "media_id": exercise.media_id,
                    "order": exercise.exercise_order,
                    "mode": exercise.prescription_mode,
                    "sets": exercise.sets,
                    "repetitions": exercise.repetitions,
                    "duration_seconds": exercise.duration_seconds,
                    "rest_seconds": exercise.rest_seconds,
                }
                for week, training_session, exercise in rows
            ],
        }
    )
    if content_hash != body.content_hash:
        raise DomainValidationError("version content_hash mismatch")
    if (
        int(job.input_tokens or 0) != body.input_tokens
        or int(job.output_tokens or 0) != body.output_tokens
        or Decimal(job.estimated_cost or 0) != body.estimated_cost
    ):
        raise DomainValidationError("generation usage totals mismatch")
    now = utc_now()
    version.content_hash = content_hash
    version.status = "validating"
    version.generation_completed_at = now
    version.updated_at = now
    weeks = list(
        (
            await session.scalars(
                select(LeoFTTrainingWeek)
                .where(
                    LeoFTTrainingWeek.version_id == job.target_version_id,
                )
                .with_for_update()
            )
        ).all()
    )
    for week in weeks:
        week.status = "completed"
        week.updated_at = _legacy_utc_now()
    if job.status == GenerationJobStatus.GENERATING.value:
        job.status = validate_job_transition(
            job.status, GenerationJobStatus.VALIDATING
        ).value
    elif job.status != GenerationJobStatus.VALIDATING.value:
        raise InvalidJobTransitionError(
            "generation completion requires generating/validating job"
        )
    job.current_stage = "generation_completed"
    job.row_version += 1
    job.updated_at = now
    await session.flush()
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            stage="generation_completed",
            blueprint_hash=body.expected_blueprint_hash,
        ),
    )
    return GenerationCompletionResponse(
        generation_job_id=job.generation_job_id,
        version_id=version.id,
        content_hash=content_hash,
        status=job.status,
        row_version=job.row_version,
    )


async def submit_review(
    session: AsyncSession,
    job_id: UUID,
    body: SubmitReviewRequest,
    *,
    idempotency_key: str,
    request_hash: str,
) -> tuple[GenerationCompletionResponse, LeoFTGenerationJob]:
    job = await find_job(session, job_id, lock=True)
    version = await session.scalar(
        select(LeoFTTrainingPlanVersion)
        .where(
            LeoFTTrainingPlanVersion.id == job.target_version_id,
            LeoFTTrainingPlanVersion.plan_id == job.plan_id,
        )
        .with_for_update()
    )
    if version is None:
        raise ResourceNotFoundError("version not found")
    storage_key = f"{job.company_id}:submit-review:{idempotency_key}"
    existing_event = await session.scalar(
        select(LeoFTIntegrationEvent)
        .where(
            LeoFTIntegrationEvent.integration == "leoft_generation_review",
            LeoFTIntegrationEvent.idempotency_key == storage_key,
        )
        .with_for_update()
    )
    if existing_event is not None:
        if existing_event.request_hash != request_hash:
            raise IdempotencyConflictError()
        payload = dict(existing_event.response_payload_sanitized or {})
        return GenerationCompletionResponse.model_validate(payload), job
    _check_version(job.row_version, body.expected_job_row_version)
    if version.content_hash != body.expected_content_hash:
        raise IdempotencyConflictError("content hash mismatch")
    if (
        version.status == "awaiting_review"
        and job.status == GenerationJobStatus.AWAITING_REVIEW.value
    ):
        return GenerationCompletionResponse(
            generation_job_id=job.generation_job_id,
            version_id=version.id,
            content_hash=version.content_hash,
            status=job.status,
            row_version=job.row_version,
        ), job
    if (
        version.status != "validating"
        or job.status != GenerationJobStatus.VALIDATING.value
    ):
        raise InvalidJobTransitionError("submit review requires validated generation")
    now = utc_now()
    version.status = "awaiting_review"
    version.submitted_for_review_at = now
    version.updated_at = now
    job.status = validate_job_transition(
        job.status, GenerationJobStatus.AWAITING_REVIEW
    ).value
    job.current_stage = "awaiting_review"
    job.row_version += 1
    job.updated_at = now
    event_now = _legacy_utc_now()
    review_event = LeoFTIntegrationEvent(
        direction="internal",
        integration="leoft_generation_review",
        event_type="leoft.generation.awaiting_review.v1",
        external_event_id=str(job.generation_job_id),
        idempotency_key=storage_key,
        plan_id=job.plan_id,
        request_hash=request_hash,
        request_payload_sanitized={
            "generation_job_id": str(job.generation_job_id),
            "version_id": str(version.id),
            "content_hash": version.content_hash,
            "correlation_id": str(job.correlation_id),
        },
        status="completed",
        response_status=200,
        processed_at=event_now,
        created_at=event_now,
        updated_at=event_now,
    )
    session.add(review_event)
    await session.flush()
    await create_checkpoint(
        session,
        job,
        CheckpointPostRequest(
            stage="awaiting_review",
            blueprint_hash=canonical_sha256(version.blueprint or {}),
        ),
    )
    response = GenerationCompletionResponse(
        generation_job_id=job.generation_job_id,
        version_id=version.id,
        content_hash=version.content_hash,
        status=job.status,
        row_version=job.row_version,
    )
    review_event.response_payload_sanitized = response.model_dump(mode="json")
    return response, job
