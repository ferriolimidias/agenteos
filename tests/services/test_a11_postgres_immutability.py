"""PostgreSQL lifecycle contract; run only against an isolated A11 test database."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.a11_prompt_optimizer import experiment_metrics


DATABASE_URL = os.getenv("A11_POSTGRES_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="explicit isolated A11 PostgreSQL test database required")


@pytest.mark.asyncio
async def test_prompt_versions_are_immutable_and_tenant_cascade_is_allowed():
    if "a11_" not in DATABASE_URL.rsplit("/", 1)[-1]:
        pytest.fail("A11_POSTGRES_TEST_DATABASE_URL must use a dedicated a11_ database")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    tenant_id, source_id, candidate_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO empresas(id) VALUES (:tenant_id)"), {"tenant_id": tenant_id})
            await connection.execute(text("""
                INSERT INTO a11_prompt_versions
                    (id, empresa_id, module_key, version_number, content, content_hash, change_summary, hypothesis, primary_metric)
                VALUES
                    (:source_id, :tenant_id, 'qualification', 1, 'base', :source_hash, 'base', 'base', 'response_after_7d'),
                    (:candidate_id, :tenant_id, 'qualification', 2, 'candidate', :candidate_hash, 'change', 'hypothesis', 'response_after_7d')
            """), {
                "source_id": source_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
                "source_hash": "a" * 64,
                "candidate_hash": "b" * 64,
            })

        async with engine.begin() as connection:
            savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(text("UPDATE a11_prompt_versions SET content='mutated' WHERE id=:id"), {"id": source_id})
            await savepoint.rollback()
            savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(text("DELETE FROM a11_prompt_versions WHERE id=:id"), {"id": source_id})
            await savepoint.rollback()
            content = await connection.scalar(text("SELECT content FROM a11_prompt_versions WHERE id=:id"), {"id": source_id})
            assert content == "base"

        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM empresas WHERE id=:tenant_id"), {"tenant_id": tenant_id})
            remaining = await connection.scalar(text("SELECT count(*) FROM a11_prompt_versions WHERE empresa_id=:tenant_id"), {"tenant_id": tenant_id})
            assert remaining == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM empresas WHERE id=:tenant_id"), {"tenant_id": tenant_id})
        await engine.dispose()


@pytest.mark.asyncio
async def test_experiment_metrics_use_persisted_tenant_scoped_rows_and_observe_zero():
    if "a11_" not in DATABASE_URL.rsplit("/", 1)[-1]:
        pytest.fail("A11_POSTGRES_TEST_DATABASE_URL must use a dedicated a11_ database")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    tenant_id, other_tenant_id = uuid.uuid4(), uuid.uuid4()
    experiment_id, control_version_id, candidate_version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    control_assignment_id, candidate_assignment_id = uuid.uuid4(), uuid.uuid4()
    control_conversation_id, candidate_conversation_id = uuid.uuid4(), uuid.uuid4()
    control_lead_id, candidate_lead_id = uuid.uuid4(), uuid.uuid4()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS channel_conversations (
                    id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE, lead_id UUID
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS mensagens_historico (
                    id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                    channel_conversation_id UUID, from_me BOOLEAN NOT NULL, criado_em TIMESTAMP NOT NULL
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS commercial_events (
                    id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                    lead_id UUID NOT NULL, event_name VARCHAR(80) NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL, revenue_verified BOOLEAN NOT NULL DEFAULT FALSE
                )
            """))
            await connection.execute(text("INSERT INTO empresas(id) VALUES (:id), (:other_id)"), {"id": tenant_id, "other_id": other_tenant_id})
            now = await connection.scalar(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::timestamp"))
            exposed_at = now - timedelta(days=40)
            await connection.execute(text("""
                INSERT INTO a11_prompt_versions
                    (id, empresa_id, module_key, version_number, content, content_hash, change_summary, hypothesis, primary_metric)
                VALUES
                    (:control_id, :tenant_id, 'qualification', 1, 'control', :control_hash, 'base', 'base', 'response_after_7d'),
                    (:candidate_id, :tenant_id, 'qualification', 2, 'candidate', :candidate_hash, 'change', 'hypothesis', 'response_after_7d')
            """), {"control_id": control_version_id, "candidate_id": candidate_version_id, "tenant_id": tenant_id, "control_hash": "c" * 64, "candidate_hash": "d" * 64})
            await connection.execute(text("""
                INSERT INTO a11_prompt_experiments
                    (id, empresa_id, module_key, control_version_id, candidate_version_id, status, candidate_percent, criteria_json)
                VALUES (:id, :tenant_id, 'qualification', :control_version_id, :candidate_version_id, 'RUNNING', 50, CAST(:criteria AS jsonb))
            """), {"id": experiment_id, "tenant_id": tenant_id, "control_version_id": control_version_id, "candidate_version_id": candidate_version_id, "criteria": '{"maturity_days":7}'})
            await connection.execute(text("""
                INSERT INTO channel_conversations(id, empresa_id, lead_id)
                VALUES (:control_conversation, :tenant_id, :control_lead), (:candidate_conversation, :tenant_id, :candidate_lead)
            """), {"control_conversation": control_conversation_id, "candidate_conversation": candidate_conversation_id, "tenant_id": tenant_id, "control_lead": control_lead_id, "candidate_lead": candidate_lead_id})
            await connection.execute(text("""
                INSERT INTO a11_prompt_assignments
                    (id, empresa_id, experiment_id, conversation_id, variant, prompt_version_id, assignment_hash, status,
                     generation_attempt_count, generation_failure_count, guard_block_count)
                VALUES
                    (:control_assignment, :tenant_id, :experiment_id, :control_conversation, 'control', :control_version, :control_hash, 'exposed', 1, 0, 0),
                    (:candidate_assignment, :tenant_id, :experiment_id, :candidate_conversation, 'candidate', :candidate_version, :candidate_hash, 'exposed', 1, 0, 0)
            """), {"control_assignment": control_assignment_id, "candidate_assignment": candidate_assignment_id, "tenant_id": tenant_id, "experiment_id": experiment_id, "control_conversation": control_conversation_id, "candidate_conversation": candidate_conversation_id, "control_version": control_version_id, "candidate_version": candidate_version_id, "control_hash": "e" * 64, "candidate_hash": "f" * 64})
            await connection.execute(text("""
                INSERT INTO a11_prompt_exposures
                    (id, empresa_id, experiment_id, assignment_id, conversation_id, variant, prompt_version_id, assignment_hash,
                     exposed_at, generation_latency_ms, input_tokens, output_tokens, guard_allowed)
                VALUES
                    (:control_exposure, :tenant_id, :experiment_id, :control_assignment, :control_conversation, 'control', :control_version, :control_hash, :exposed_at, 100, 20, 10, TRUE),
                    (:candidate_exposure, :tenant_id, :experiment_id, :candidate_assignment, :candidate_conversation, 'candidate', :candidate_version, :candidate_hash, :exposed_at, 100, 20, 10, TRUE)
            """), {"control_exposure": uuid.uuid4(), "candidate_exposure": uuid.uuid4(), "tenant_id": tenant_id, "experiment_id": experiment_id, "control_assignment": control_assignment_id, "candidate_assignment": candidate_assignment_id, "control_conversation": control_conversation_id, "candidate_conversation": candidate_conversation_id, "control_version": control_version_id, "candidate_version": candidate_version_id, "control_hash": "e" * 64, "candidate_hash": "f" * 64, "exposed_at": exposed_at})
            await connection.execute(text("""
                INSERT INTO mensagens_historico(id, empresa_id, channel_conversation_id, from_me, criado_em)
                VALUES (:id, :tenant_id, :conversation_id, FALSE, :response_at),
                       (:other_id, :other_tenant, :conversation_id, FALSE, :response_at)
            """), {"id": uuid.uuid4(), "other_id": uuid.uuid4(), "tenant_id": tenant_id, "other_tenant": other_tenant_id, "conversation_id": control_conversation_id, "response_at": exposed_at + timedelta(days=1)})
            await connection.execute(text("""
                INSERT INTO commercial_events(id, empresa_id, lead_id, event_name, occurred_at, revenue_verified)
                VALUES (:id, :tenant_id, :lead_id, 'Purchase', :occurred_at, TRUE),
                       (:other_id, :other_tenant, :lead_id, 'Purchase', :occurred_at, TRUE)
            """), {"id": uuid.uuid4(), "other_id": uuid.uuid4(), "tenant_id": tenant_id, "other_tenant": other_tenant_id, "lead_id": control_lead_id, "occurred_at": exposed_at.replace(tzinfo=timezone.utc) + timedelta(days=5)})

        async with session_factory() as session:
            metrics = await experiment_metrics(session, tenant_id, type("Experiment", (), {"id": experiment_id, "criteria_json": {"maturity_days": 7}, "status": "RUNNING", "candidate_percent": 50})())
        assert metrics["assignments"] == {"numerator": 2, "denominator": 2, "coverage": 1.0, "state": "observed"}
        assert metrics["by_variant"]["control"]["response_after_window"]["numerator"] == 1
        assert metrics["by_variant"]["candidate"]["response_after_window"]["numerator"] == 0
        assert metrics["by_variant"]["candidate"]["response_after_window"]["state"] == "observed"
        assert metrics["by_variant"]["control"]["verified_purchase_after_30d"]["numerator"] == 1
        assert metrics["by_variant"]["candidate"]["verified_purchase_after_30d"]["numerator"] == 0
        assert metrics["by_variant"]["candidate"]["verified_purchase_after_30d"]["state"] == "observed"
        assert metrics["truncated"] is False
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM empresas WHERE id IN (:id, :other_id)"), {"id": tenant_id, "other_id": other_tenant_id})
        await engine.dispose()
