"""Transactional S4A vertical test against the configured real AgentOS PostgreSQL."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.leoft_generation.schemas import TrainingBlueprint
from app.leoft_generation.services import canonical_sha256
from app.schemas_leoft_generation_api import (
    AttemptPostRequest,
    BlueprintPutRequest,
    CompleteGenerationRequest,
    ExercisePrescription,
    SessionPutRequest,
    SubmitReviewRequest,
    TaskFailureRequest,
)
from app.services import leoft_generation_api_service as service
from app.services.leoft_generation_api_service import blueprint_hash_for_wire

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    value = os.getenv("LEOFT_GENERATION_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("real AgentOS PostgreSQL was not explicitly configured")
    assert value.startswith(("postgresql://", "postgresql+asyncpg://"))
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed(
    session: AsyncSession,
    *,
    status: str = "planning",
    current_stage: str = "planning",
    row_version: int = 0,
) -> dict:
    company_id = await session.scalar(
        text("SELECT id FROM empresas ORDER BY id LIMIT 1")
    )
    assert company_id is not None
    ids = {
        "company": company_id,
        "student": uuid4(),
        "payment": uuid4(),
        "event": uuid4(),
        "snapshot": uuid4(),
        "plan": uuid4(),
        "version": uuid4(),
        "job": uuid4(),
        "correlation": uuid4(),
        "status": status,
        "current_stage": current_stage,
        "row_version": row_version,
    }
    marker = f"E2E-S4A-{ids['job']}"
    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            "INSERT INTO crm_leads "
            "(id, empresa_id, nome_contato, tags, status_atendimento, ia_ativa) "
            "VALUES (:student, :company, :marker, '[]'::jsonb, 'e2e_s4a', false)"
        ),
        {**ids, "marker": marker},
    )
    await session.execute(
        text(
            "INSERT INTO leoft_payment_events "
            "(id,event_id,event_type,occurred_at,received_at,company_id,contact_id,student_id,"
            "product_id,order_id,payment_id,provider,provider_event_id,amount,currency,"
            "payment_status,environment,correlation_id,idempotency_key,raw_event_hash,processing_status) "
            "VALUES (:payment,:event,'leoft.payment_confirmed.v1',:now,:now,:company,:student,:student,"
            "'leoft.metodo_hibrido',:marker,:marker,'controlled_e2e',:marker,97,'BRL',"
            "'confirmed','e2e',:correlation,:idem,:raw_hash,'job_created')"
        ),
        {
            **ids,
            "now": now,
            "marker": marker,
            "idem": canonical_sha256({"idempotency": marker}),
            "raw_hash": canonical_sha256({"raw": marker}),
        },
    )
    await session.execute(
        text(
            "INSERT INTO leoft_anamnesis_snapshots "
            "(snapshot_id,company_id,student_id,anamnesis_id,anamnesis_version,source_hash,captured_at,"
            "goal,experience_level,weekly_frequency,available_session_duration,available_equipment,"
            "limitations,injuries,preferences,restrictions,product_rules,requested_duration_weeks,"
            "sessions_per_week,additional_answers,schema_version) "
            "VALUES (:snapshot,:company,:student,:marker,'e2e-v1',:hash,:now,'E2E controlled','beginner',"
            "3,45,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,'[]'::jsonb,'{}'::jsonb,4,3,"
            "'{}'::jsonb,'leoft.anamnesis-snapshot.v1')"
        ),
        {
            **ids,
            "now": now,
            "marker": marker,
            "hash": canonical_sha256({"marker": marker}),
        },
    )
    await session.execute(
        text(
            "INSERT INTO leoft_training_plans "
            "(id,empresa_id,lead_id,product_name,product_id,status,plan_json,hermes_mode,telegram_status) "
            "VALUES (:plan,:company,:student,'E2E S4A','leoft.metodo_hibrido','generating','{}'::jsonb,"
            "'real','disabled')"
        ),
        ids,
    )
    await session.execute(
        text(
            "INSERT INTO leoft_training_plan_versions "
            "(id,plan_id,version_number,schema_version,source,status,snapshot_json,source_snapshot_id,checksum,created_by) "
            "VALUES (:version,:plan,1,'leoft.training-plan.v2','e2e_s4a','building','{}'::jsonb,:snapshot,:hash,'e2e_s4a')"
        ),
        {**ids, "hash": "0" * 64},
    )
    await session.execute(
        text(
            "INSERT INTO leoft_generation_jobs "
            "(generation_job_id,company_id,student_id,product_id,order_id,payment_event_id,"
            "anamnesis_snapshot_id,plan_id,target_version_id,status,current_stage,requested_duration_weeks,"
            "sessions_per_week,correlation_id,idempotency_key,maximum_attempts,row_version) "
            "VALUES (:job,:company,:student,'leoft.metodo_hibrido',:marker,:payment,:snapshot,"
            ":plan,:version,:status,:current_stage,4,3,:correlation,:hash,3,:row_version)"
        ),
        {**ids, "marker": marker, "hash": canonical_sha256({"job": str(ids["job"])})},
    )
    await session.execute(
        text(
            "UPDATE leoft_training_plans SET generation_job_id=:job,current_version_id=:version WHERE id=:plan"
        ),
        ids,
    )
    pair = (
        await session.execute(
            text(
                "SELECT e.catalog_id,m.media_id,v.version "
                "FROM leoft_catalog_entries e JOIN leoft_catalog_media m ON m.catalog_entry_id=e.id "
                "CROSS JOIN LATERAL (SELECT version FROM leoft_catalog_versions WHERE status='imported' "
                "ORDER BY activated_at DESC NULLS LAST LIMIT 1) v "
                "WHERE e.approval_status NOT IN ('rejected','duplicate') "
                "AND m.storage_path NOT LIKE '/%' AND m.storage_path NOT LIKE '%://%' "
                "AND m.storage_path NOT LIKE '%..%' LIMIT 1"
            )
        )
    ).first()
    assert pair is not None
    ids.update(catalog_id=pair[0], media_id=pair[1], catalog_version=pair[2])
    await session.flush()
    return ids


def _blueprint() -> dict:
    sessions = []
    sequence = 0
    for week in range(1, 5):
        for session_number in range(1, 4):
            sequence += 1
            sessions.append(
                {
                    "week_number": week,
                    "session_number": session_number,
                    "phase_number": 1 if week < 3 else 2,
                    "sequence": sequence,
                    "session_code": f"W{week:02d}S{session_number:02d}",
                    "objective": f"E2E session {sequence}",
                }
            )
    return {"schema_version": "leoft.training-plan.v2", "sessions": sessions}


def _blueprint_without_session_codes() -> dict:
    blueprint = _blueprint()
    return {
        **blueprint,
        "sessions": [
            {key: value for key, value in item.items() if key != "session_code"}
            for item in blueprint["sessions"]
        ],
    }


def _blueprint_with_mixed_session_codes() -> dict:
    blueprint = _blueprint()
    return {
        **blueprint,
        "sessions": [
            {
                **item,
                "session_code": (
                    item["session_code"] if item["sequence"] % 2 else None
                ),
            }
            for item in blueprint["sessions"]
        ],
    }


def _blueprint_request(
    ids: dict,
    blueprint: dict,
    *,
    expected_row_version: int = 3,
    expected_checkpoint_version: int = 0,
    target_version_key: str = "version",
) -> BlueprintPutRequest:
    return BlueprintPutRequest(
        target_version_id=ids[target_version_key],
        expected_row_version=expected_row_version,
        expected_checkpoint_version=expected_checkpoint_version,
        blueprint=blueprint,
        blueprint_hash=blueprint_hash_for_wire(
            TrainingBlueprint.model_validate(blueprint)
        ),
        schema_version="leoft.training-plan.v2",
        model_provider="deepseek",
        model_name="deepseek-chat",
        input_tokens=1,
        output_tokens=1,
        estimated_cost=Decimal("0.00000001"),
        correlation_id=ids["correlation"],
    )


@pytest.mark.timeout(60)
async def test_dispatching_blueprint_put_accepts_hermes_hash_without_session_codes() -> (
    None
):
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        ids = await _seed(
            db,
            status="dispatching",
            current_stage="dispatch_accepted",
            row_version=3,
        )
        blueprint = _blueprint_without_session_codes()
        request = _blueprint_request(ids, blueprint)

        job_view = await service.put_blueprint(
            db,
            ids["job"],
            request,
            idempotency_key=f"blueprint-{ids['job']}-{request.blueprint_hash}",
            request_hash=canonical_sha256({"blueprint": ids["job"], "r3": 1}),
        )

        assert job_view.status == "planning"
        assert job_view.current_stage == "blueprint_persisted"
        assert job_view.row_version == 5
        assert job_view.blueprint["sessions"][0]["session_code"] == "W01S01"
        stored = await service.find_job(db, ids["job"])
        assert stored.row_version == 5

        repeated = await service.put_blueprint(
            db,
            ids["job"],
            request,
            idempotency_key=f"blueprint-{ids['job']}-{request.blueprint_hash}",
            request_hash=canonical_sha256({"blueprint": ids["job"], "r3": 1}),
        )
        assert repeated.row_version == 5
        checkpoints = await db.scalar(
            text(
                "SELECT count(*) FROM leoft_generation_checkpoints "
                "WHERE generation_job_id=:job"
            ),
            ids,
        )
        assert checkpoints == 1
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "blueprint_factory",
    [_blueprint, _blueprint_without_session_codes, _blueprint_with_mixed_session_codes],
)
async def test_hermes_wire_blueprints_pass_put_blueprint_inside_rollback_only(
    blueprint_factory,
) -> None:
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        ids = await _seed(
            db,
            status="dispatching",
            current_stage="dispatch_accepted",
            row_version=5,
        )
        blueprint = blueprint_factory()
        request = _blueprint_request(ids, blueprint, expected_row_version=5)
        result = await service.put_blueprint(
            db,
            ids["job"],
            request,
            idempotency_key=f"rollback-only-{ids['job']}",
            request_hash=canonical_sha256({"job": str(ids["job"]), "r5": 1}),
        )
        assert result.status == "planning"
        assert result.current_stage == "blueprint_persisted"
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.timeout(60)
@pytest.mark.parametrize("expected_row_version", [2, 4])
async def test_dispatching_blueprint_put_rejects_stale_or_premature_row_version(
    expected_row_version: int,
) -> None:
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        ids = await _seed(
            db,
            status="dispatching",
            current_stage="dispatch_accepted",
            row_version=3,
        )
        request = _blueprint_request(
            ids,
            _blueprint_without_session_codes(),
            expected_row_version=expected_row_version,
        )
        with pytest.raises(service.OptimisticLockError):
            await service.put_blueprint(
                db,
                ids["job"],
                request,
                idempotency_key=f"blueprint-{ids['job']}-{expected_row_version}",
                request_hash=canonical_sha256(
                    {"blueprint": ids["job"], "row": expected_row_version}
                ),
            )
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.timeout(60)
async def test_dispatching_blueprint_put_rejects_wrong_target_checkpoint_and_terminal_status() -> (
    None
):
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        ids = await _seed(
            db,
            status="dispatching",
            current_stage="dispatch_accepted",
            row_version=3,
        )
        wrong_version = uuid4()
        ids["wrong_version"] = wrong_version
        request = _blueprint_request(
            ids, _blueprint_without_session_codes(), target_version_key="wrong_version"
        )
        with pytest.raises(service.DomainValidationError):
            await service.put_blueprint(
                db,
                ids["job"],
                request,
                idempotency_key=f"blueprint-{ids['job']}-wrong-target",
                request_hash=canonical_sha256({"blueprint": ids["job"], "wrong": 1}),
            )

        request = _blueprint_request(
            ids, _blueprint_without_session_codes(), expected_checkpoint_version=1
        )
        with pytest.raises(service.OptimisticLockError):
            await service.put_blueprint(
                db,
                ids["job"],
                request,
                idempotency_key=f"blueprint-{ids['job']}-wrong-checkpoint",
                request_hash=canonical_sha256({"blueprint": ids["job"], "wrong": 2}),
            )

        await db.execute(
            text(
                "UPDATE leoft_generation_jobs "
                "SET status='failed', current_stage='failed', row_version=3 "
                "WHERE generation_job_id=:job"
            ),
            ids,
        )
        request = _blueprint_request(ids, _blueprint_without_session_codes())
        with pytest.raises(service.InvalidJobTransitionError):
            await service.put_blueprint(
                db,
                ids["job"],
                request,
                idempotency_key=f"blueprint-{ids['job']}-failed",
                request_hash=canonical_sha256({"blueprint": ids["job"], "failed": 1}),
            )

        ids_published = await _seed(
            db,
            status="dispatching",
            current_stage="dispatch_accepted",
            row_version=3,
        )
        for status in (
            "planning",
            "generating",
            "validating",
            "awaiting_review",
            "approved",
            "publishing",
            "published",
        ):
            await db.execute(
                text(
                    "UPDATE leoft_generation_jobs "
                    "SET status=:status, current_stage=:status, row_version=3 "
                    "WHERE generation_job_id=:job"
                ),
                {**ids_published, "status": status},
            )
        request = _blueprint_request(ids_published, _blueprint_without_session_codes())
        with pytest.raises(service.InvalidJobTransitionError):
            await service.put_blueprint(
                db,
                ids_published["job"],
                request,
                idempotency_key=f"blueprint-{ids_published['job']}-published",
                request_hash=canonical_sha256(
                    {"blueprint": ids_published["job"], "published": 1}
                ),
            )
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


def _attempt(
    task, ids, *, status: str = "succeeded", number_hint: int = 1
) -> AttemptPostRequest:
    now = datetime.now(timezone.utc)
    return AttemptPostRequest(
        expected_task_row_version=task.row_version,
        attempt_type="generation",
        stage=f"session:{task.session_code}",
        prompt_version="e2e-s4a-v1",
        catalog_version=ids["catalog_version"],
        status=status,
        provider="controlled_e2e",
        model="no_external_model_called",
        input_tokens=1,
        output_tokens=1,
        estimated_cost=Decimal("0.00000001"),
        request_hash=canonical_sha256(
            {"task": str(task.session_task_id), "n": number_hint}
        ),
        response_hash=(
            canonical_sha256({"response": str(task.session_task_id), "n": number_hint})
            if status == "succeeded"
            else None
        ),
        error_code=None if status == "succeeded" else "controlled_failure",
        error_summary=None if status == "succeeded" else "controlled retry validation",
        started_at=now,
        finished_at=now,
        correlation_id=ids["correlation"],
    )


def _session_body(task, attempt_id, ids) -> SessionPutRequest:
    exercise = ExercisePrescription(
        external_exercise_id=f"E2E-{task.session_code}-01",
        order=1,
        catalog_id=ids["catalog_id"],
        media_id=ids["media_id"],
        catalog_version=ids["catalog_version"],
        prescription_mode="repetitions",
        sets=3,
        repetitions="8-10",
        rest_seconds=60,
        selection_reason="Controlled E2E catalog association",
    )
    data = {
        "target_version_id": ids["version"],
        "week_number": task.week_number,
        "session_number": task.session_number,
        "session_code": task.session_code,
        "sequence": task.sequence,
        "expected_row_version": task.row_version,
        "lease_owner": "e2e-s4a-worker",
        "title": f"E2E {task.session_code}",
        "objective": task.objective,
        "estimated_duration_minutes": 45,
        "notes": {},
        "week_objective": "Controlled E2E week",
        "exercises": [exercise],
        "validation": {"status": "passed"},
        "attempt_id": attempt_id,
        "correlation_id": ids["correlation"],
    }
    provisional = SessionPutRequest(**data, content_hash="0" * 64)
    hashed = provisional.model_dump(
        mode="json",
        exclude={"expected_row_version", "lease_owner", "content_hash"},
    )
    return provisional.model_copy(update={"content_hash": canonical_sha256(hashed)})


@pytest.mark.timeout(60)
async def test_real_postgres_full_incremental_flow_is_transactional() -> None:
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        ids = await _seed(db)
        blueprint = _blueprint()
        blueprint_hash = blueprint_hash_for_wire(
            TrainingBlueprint.model_validate(blueprint)
        )
        job_view = await service.put_blueprint(
            db,
            ids["job"],
            BlueprintPutRequest(
                target_version_id=ids["version"],
                expected_row_version=0,
                expected_checkpoint_version=0,
                blueprint=blueprint,
                blueprint_hash=blueprint_hash,
                schema_version="leoft.training-plan.v2",
                model_provider="controlled_e2e",
                model_name="no_external_model_called",
                input_tokens=1,
                output_tokens=1,
                estimated_cost=Decimal("0.00000001"),
                correlation_id=ids["correlation"],
            ),
            idempotency_key=f"blueprint-{ids['job']}",
            request_hash=canonical_sha256(blueprint),
        )
        tasks = await service.initialize_tasks(
            db, ids["job"], expected_row_version=job_view.row_version
        )
        assert len(tasks) == 12
        job_after_initialize = await service.find_job(db, ids["job"])
        assert (
            len(
                await service.initialize_tasks(
                    db,
                    ids["job"],
                    expected_row_version=job_after_initialize.row_version,
                )
            )
            == 12
        )

        first, job = await service.find_task_with_job(
            db, tasks[0].session_task_id, lock=True
        )
        acquired = await service.acquire_specific_task(
            db, first, lease_owner="e2e-s4a-worker"
        )
        first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.flush()
        reacquired = await service.acquire_specific_task(
            db, first, lease_owner="e2e-s4a-worker-2"
        )
        assert reacquired.row_version > acquired.row_version
        await service.release_task(
            db, first, owner="e2e-s4a-worker-2", expected_row_version=first.row_version
        )
        await service.acquire_specific_task(db, first, lease_owner="e2e-s4a-worker")
        failed_attempt = await service.append_attempt(
            db,
            first,
            job,
            _attempt(first, ids, status="provider_failed"),
            idempotency_key=f"failed-{first.session_task_id}",
        )
        failed = await service.fail_task(
            db,
            first,
            job,
            TaskFailureRequest(
                lease_owner="e2e-s4a-worker",
                expected_row_version=first.row_version,
                error_code="controlled_failure",
                error_summary="controlled retry validation",
                retryable=True,
                attempt_id=failed_attempt.attempt_id,
                correlation_id=ids["correlation"],
            ),
        )
        assert failed.status == "pending"

        for item in tasks:
            task, job = await service.find_task_with_job(
                db, item.session_task_id, lock=True
            )
            if task.status == "pending":
                await service.acquire_specific_task(
                    db, task, lease_owner="e2e-s4a-worker"
                )
            attempt_body = _attempt(task, ids, number_hint=task.attempt_count + 1)
            attempt = await service.append_attempt(
                db,
                task,
                job,
                attempt_body,
                idempotency_key=f"success-{task.session_task_id}",
            )
            repeated = await service.append_attempt(
                db,
                task,
                job,
                attempt_body,
                idempotency_key=f"success-{task.session_task_id}",
            )
            assert repeated.attempt_id == attempt.attempt_id
            body = _session_body(task, attempt.attempt_id, ids)
            if task.sequence == 1:
                invalid = body.model_copy(deep=True)
                invalid.exercises[0].catalog_id = "E2E-INVALID-CATALOG"
                invalid.content_hash = canonical_sha256(
                    invalid.model_dump(
                        mode="json",
                        exclude={"expected_row_version", "lease_owner", "content_hash"},
                    )
                )
                with pytest.raises(Exception, match="catalog/media association"):
                    await service.put_session(db, task, job, invalid)
                invalid = body.model_copy(deep=True)
                invalid.exercises[0].media_id = "E2E-INVALID-MEDIA"
                invalid.content_hash = canonical_sha256(
                    invalid.model_dump(
                        mode="json",
                        exclude={"expected_row_version", "lease_owner", "content_hash"},
                    )
                )
                with pytest.raises(Exception, match="catalog/media association"):
                    await service.put_session(db, task, job, invalid)
            result = await service.put_session(db, task, job, body)
            assert result.exercise_count == 1

        job = await service.find_job(db, ids["job"], lock=True)
        rows = (
            await db.execute(
                text(
                    "SELECT w.week_number,w.phase_number,s.session_code,e.external_exercise_id,"
                    'e.catalog_id,e.media_id,e."order",e.prescription_mode,e.sets,e.repetitions,'
                    "e.duration_seconds,e.rest_seconds FROM leoft_training_weeks w "
                    "JOIN leoft_training_sessions s ON s.week_id=w.id "
                    "JOIN leoft_training_exercises e ON e.session_id=s.id "
                    'WHERE w.version_id=:version ORDER BY w.week_number,s.sequence,e."order"'
                ),
                ids,
            )
        ).all()
        content_hash = canonical_sha256(
            {
                "blueprint": blueprint,
                "content": [
                    {
                        "week": row[0],
                        "phase": row[1],
                        "session": row[2],
                        "exercise": row[3],
                        "catalog_id": row[4],
                        "media_id": row[5],
                        "order": row[6],
                        "mode": row[7],
                        "sets": row[8],
                        "repetitions": row[9],
                        "duration_seconds": row[10],
                        "rest_seconds": row[11],
                    }
                    for row in rows
                ],
            }
        )
        completed = await service.complete_generation(
            db,
            ids["job"],
            CompleteGenerationRequest(
                expected_row_version=job.row_version,
                expected_blueprint_hash=blueprint_hash,
                content_hash=content_hash,
                input_tokens=job.input_tokens,
                output_tokens=job.output_tokens,
                estimated_cost=job.estimated_cost,
                correlation_id=ids["correlation"],
            ),
        )
        assert completed.status == "validating"
        reviewed, _ = await service.submit_review(
            db,
            ids["job"],
            SubmitReviewRequest(
                expected_job_row_version=completed.row_version,
                expected_content_hash=content_hash,
            ),
            idempotency_key=f"review-{ids['job']}",
            request_hash=canonical_sha256({"content_hash": content_hash}),
        )
        assert reviewed.status == "awaiting_review"
        assert (
            await service.latest_checkpoint(db, await service.find_job(db, ids["job"]))
        ).stage == "awaiting_review"
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
