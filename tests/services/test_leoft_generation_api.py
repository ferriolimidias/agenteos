from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routers.leoft_generation_internal import router
from app.leoft_generation.services import canonical_sha256
from app.schemas_leoft_generation_api import (
    AttemptPostRequest,
    BlueprintPutRequest,
    ExercisePrescription,
    JobResponse,
    SessionPutRequest,
    TaskFailureRequest,
)
from app.services import leoft_generation_api_service as service


def _blueprint() -> dict:
    return {
        "schema_version": "leoft.training-plan.v2",
        "sessions": [
            {
                "week_number": 1,
                "session_number": 1,
                "phase_number": 1,
                "sequence": 1,
                "objective": "Força geral",
            }
        ],
    }


def test_router_exposes_only_frozen_s4a_paths() -> None:
    routes = {(route.path, frozenset(route.methods)) for route in router.routes}
    expected = {
        ("/generation/v1/jobs/{job_id}", frozenset({"GET"})),
        ("/generation/v1/jobs/{job_id}/blueprint", frozenset({"PUT"})),
        ("/generation/v1/jobs/{job_id}/tasks/initialize", frozenset({"POST"})),
        ("/generation/v1/jobs/{job_id}/tasks", frozenset({"GET"})),
        ("/generation/v1/tasks/{task_id}/acquire", frozenset({"POST"})),
        ("/generation/v1/tasks/{task_id}/heartbeat", frozenset({"POST"})),
        ("/generation/v1/tasks/{task_id}/release", frozenset({"POST"})),
        ("/generation/v1/tasks/{task_id}/attempts", frozenset({"POST"})),
        ("/generation/v1/tasks/{task_id}/session", frozenset({"PUT"})),
        ("/generation/v1/tasks/{task_id}/fail", frozenset({"POST"})),
        ("/generation/v1/jobs/{job_id}/checkpoints/latest", frozenset({"GET"})),
        ("/generation/v1/jobs/{job_id}/checkpoints", frozenset({"POST"})),
        ("/generation/v1/jobs/{job_id}/complete", frozenset({"POST"})),
        ("/generation/v1/jobs/{job_id}/submit-review", frozenset({"POST"})),
    }
    assert routes == expected


def test_blueprint_contract_requires_frozen_concurrency_and_usage_fields() -> None:
    blueprint = _blueprint()
    payload = BlueprintPutRequest(
        target_version_id=uuid4(),
        expected_row_version=2,
        expected_checkpoint_version=1,
        blueprint=blueprint,
        blueprint_hash=canonical_sha256(blueprint),
        schema_version="leoft.training-plan.v2",
        model_provider="provider",
        model_name="model",
        input_tokens=10,
        output_tokens=20,
        estimated_cost=Decimal("0.012"),
        correlation_id=uuid4(),
    )
    assert payload.blueprint.sessions[0].session_code is None
    with pytest.raises(ValidationError):
        BlueprintPutRequest(**payload.model_dump(), unexpected=True)


def test_attempt_is_strict_utc_and_correlation_bound() -> None:
    base = {
        "expected_task_row_version": 1,
        "attempt_type": "generation",
        "stage": "session:W01S01",
        "prompt_version": "v1",
        "catalog_version": "catalog-v1",
        "status": "succeeded",
        "provider": "provider",
        "model": "model",
        "request_hash": "a" * 64,
        "response_hash": "b" * 64,
        "started_at": datetime.now(timezone.utc),
        "finished_at": datetime.now(timezone.utc),
        "correlation_id": uuid4(),
    }
    assert AttemptPostRequest(**base).status.value == "succeeded"
    with pytest.raises(ValidationError):
        AttemptPostRequest(**{**base, "started_at": datetime.now()})  # noqa: DTZ005


def test_exercise_modes_and_session_contract() -> None:
    exercise = ExercisePrescription(
        external_exercise_id="exercise-1",
        order=1,
        catalog_id="catalog-1",
        media_id="media-1",
        catalog_version="catalog-v1",
        prescription_mode="repetitions",
        sets=3,
        repetitions="8-10",
        rest_seconds=60,
        selection_reason="Compatível com o objetivo",
    )
    session = SessionPutRequest(
        target_version_id=uuid4(),
        week_number=1,
        session_number=1,
        session_code="W01S01",
        sequence=1,
        expected_row_version=3,
        lease_owner="worker-1",
        title="Sessão A",
        objective="Força",
        estimated_duration_minutes=45,
        notes={},
        exercises=[exercise],
        validation={"status": "passed"},
        content_hash="c" * 64,
        attempt_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert session.exercises[0].order == 1
    with pytest.raises(ValidationError):
        ExercisePrescription(**{**exercise.model_dump(), "duration_seconds": 30})


def test_failure_contract_requires_attempt_retryability_and_correlation() -> None:
    body = TaskFailureRequest(
        lease_owner="worker-1",
        expected_row_version=4,
        error_code="provider_timeout",
        error_summary="timeout sanitizado",
        retryable=True,
        attempt_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert body.retryable is True


def test_job_response_is_minimized() -> None:
    fields = set(JobResponse.model_fields)
    assert "student_id" not in fields
    assert "anamnesis_snapshot_id" not in fields
    assert {
        "snapshot",
        "snapshot_hash",
        "schema_version",
        "task_summary",
        "latest_checkpoint",
        "limits",
    } <= fields


def test_idempotency_cache_does_not_persist_clinical_snapshot() -> None:
    event = SimpleNamespace()
    service.finish_idempotent_operation(
        event,
        {
            "generation_job_id": str(uuid4()),
            "snapshot": {"injuries": ["synthetic"], "limitations": ["synthetic"]},
        },
    )
    assert "snapshot" not in event.response_payload_sanitized


def test_real_postgres_contract_when_explicitly_configured() -> None:
    database_url = os.getenv("LEOFT_GENERATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("LEOFT_GENERATION_TEST_DATABASE_URL is not configured")
    assert database_url.startswith(("postgresql+asyncpg://", "postgresql://"))

    async def check() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                result = await connection.scalar(
                    text("SELECT to_regclass('public.leoft_generation_jobs')")
                )
                assert result == "leoft_generation_jobs"
                assert (
                    await connection.scalar(
                        text(
                            "SELECT to_regclass('public.leoft_generation_session_tasks')"
                        )
                    )
                    == "leoft_generation_session_tasks"
                )
        finally:
            await engine.dispose()

    asyncio.run(check())
