#!/usr/bin/env python3
"""Validate the LeoFT S3 migration and services in disposable PostgreSQL 16."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ankane/pgvector:latest"
APP_IMAGE = "agenteos-backend:latest"
DB_USER = "leoft_s3"
UPGRADE = ROOT / "db/migrations/2026_08_05_001_leoft_generation_foundation.sql"
ROLLBACK = (
    ROOT / "db/migrations/2026_08_05_001_leoft_generation_foundation_rollback.sql"
)
FORMAL = (
    ROOT
    / "db/migrations/2026_07_22_001_formaliza_leoft_training_plans_student_accesses.sql"
)
CENTRAL = ROOT / "db/migrations/2026_07_29_001_leoft_postgres_central.sql"
CATALOG = ROOT / "db/migrations/2026_07_31_001_leoft_catalog_foundation.sql"


def run(
    args: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args, cwd=ROOT, text=True, input=input_text, capture_output=True
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}"
        )
    return result


def docker_exec_psql(container: str, database: str, sql: str) -> None:
    run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            DB_USER,
            "-d",
            database,
        ],
        input_text=sql,
    )


def apply_file(container: str, database: str, path: pathlib.Path) -> None:
    docker_exec_psql(container, database, path.read_text(encoding="utf-8"))


def wait_ready(container: str) -> None:
    consecutive = 0
    for _ in range(60):
        result = run(
            ["docker", "exec", container, "pg_isready", "-U", DB_USER], check=False
        )
        if result.returncode == 0:
            probe = run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-tAc",
                    "SELECT 1",
                    "-U",
                    DB_USER,
                    "-d",
                    "postgres",
                ],
                check=False,
            )
            consecutive = (
                consecutive + 1
                if probe.returncode == 0 and probe.stdout.strip() == "1"
                else 0
            )
            if consecutive >= 3:
                return
        else:
            consecutive = 0
        time.sleep(0.25)
    raise RuntimeError("disposable PostgreSQL did not become ready")


def create_legacy_schema(container: str) -> None:
    docker_exec_psql(container, "postgres", "CREATE DATABASE legacy;")
    docker_exec_psql(
        container,
        "legacy",
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        CREATE TABLE empresas (id uuid PRIMARY KEY, nome_empresa text NOT NULL);
        CREATE TABLE crm_leads (
          id uuid PRIMARY KEY,
          empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
          nome_contato text NOT NULL,
          telefone_contato text,
          historico_resumo text,
          tags jsonb NOT NULL DEFAULT '[]'::jsonb,
          dados_adicionais jsonb DEFAULT '{}'::jsonb,
          status_atendimento text NOT NULL DEFAULT 'aberto',
          foto_url text,
          foto_atualizada_em timestamp,
          gclid text,
          fbclid text,
          valor_conversao double precision,
          bot_pausado_ate timestamp,
          ia_ativa boolean NOT NULL DEFAULT true,
          criado_em timestamp DEFAULT now(),
          CONSTRAINT uq_crm_leads_empresa_id_id UNIQUE (empresa_id, id)
        );
        """,
    )
    apply_file(container, "legacy", FORMAL)
    apply_file(container, "legacy", CENTRAL)
    apply_file(container, "legacy", CATALOG)
    docker_exec_psql(
        container,
        "legacy",
        """
        INSERT INTO empresas(id,nome_empresa) VALUES ('11111111-1111-4111-8111-111111111111','Legacy');
        INSERT INTO crm_leads(id,empresa_id,nome_contato,tags,status_atendimento)
          VALUES ('22222222-2222-4222-8222-222222222222','11111111-1111-4111-8111-111111111111','Legacy lead','[]','aberto');
        INSERT INTO leoft_training_plans(id,empresa_id,lead_id,plan_json,status)
          VALUES ('33333333-3333-4333-8333-333333333333','11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','{}','pending_approval');
        INSERT INTO leoft_training_plan_versions(id,plan_id,version_number,snapshot_json,checksum,status)
          VALUES ('44444444-4444-4444-8444-444444444444','33333333-3333-4333-8333-333333333333',1,'{}',repeat('a',64),'pending_approval');
        INSERT INTO leoft_training_weeks(id,plan_id,version_id,week_number,position)
          VALUES ('55555555-5555-4555-8555-555555555555','33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',1,0);
        INSERT INTO leoft_training_sessions(id,plan_id,version_id,week_id,external_session_id,position)
          VALUES ('66666666-6666-4666-8666-666666666666','33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444','55555555-5555-4555-8555-555555555555','legacy-session',0);
        INSERT INTO leoft_training_exercises(id,plan_id,version_id,session_id,external_exercise_id,position,name)
          VALUES ('77777777-7777-4777-8777-777777777777','33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444','66666666-6666-4666-8666-666666666666','legacy-exercise',0,'Legacy exercise');
        """,
    )


def validate_upgrade_and_rollback(container: str) -> None:
    apply_file(container, "legacy", UPGRADE)
    docker_exec_psql(
        container,
        "legacy",
        """
        DO $$ BEGIN
          IF (SELECT count(*) FROM leoft_training_plans) <> 1 THEN RAISE EXCEPTION 'legacy plan lost'; END IF;
          IF (SELECT count(*) FROM leoft_training_exercises WHERE position = 0) <> 1 THEN RAISE EXCEPTION 'legacy exercise changed'; END IF;
          IF to_regclass('leoft_generation_jobs') IS NULL THEN RAISE EXCEPTION 'jobs missing'; END IF;
          IF to_regclass('leoft_payment_events') IS NULL OR to_regclass('leoft_anamnesis_snapshots') IS NULL
             OR to_regclass('leoft_generation_session_tasks') IS NULL OR to_regclass('leoft_generation_attempts') IS NULL
             OR to_regclass('leoft_generation_checkpoints') IS NULL THEN RAISE EXCEPTION 'S3 table missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leoft_training_exercises' AND column_name='catalog_id') THEN RAISE EXCEPTION 'catalog_id missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_leoft_generation_jobs_company_idempotency') THEN RAISE EXCEPTION 'job idempotency missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_leoft_payment_events_tenant_reversal') THEN RAISE EXCEPTION 'tenant reversal FK missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_leoft_payment_events_provider_event') THEN RAISE EXCEPTION 'provider idempotency missing'; END IF;
          IF to_regclass('ux_leoft_generation_attempts_job_number') IS NULL THEN RAISE EXCEPTION 'job attempt uniqueness missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leoft_anamnesis_snapshots_append_only')
             OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leoft_generation_attempts_append_only')
             OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leoft_generation_checkpoints_append_only')
             THEN RAISE EXCEPTION 'append-only trigger missing'; END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leoft_generation_jobs_transition')
             OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leoft_generation_tasks_transition')
             THEN RAISE EXCEPTION 'transition trigger missing'; END IF;
        END $$;
        """,
    )
    apply_file(container, "legacy", ROLLBACK)
    docker_exec_psql(
        container,
        "legacy",
        """
        DO $$ BEGIN
          IF to_regclass('leoft_generation_jobs') IS NOT NULL THEN RAISE EXCEPTION 'jobs survived rollback'; END IF;
          IF to_regclass('ux_leoft_generation_attempts_job_number') IS NOT NULL THEN RAISE EXCEPTION 'attempt index survived rollback'; END IF;
          IF (SELECT count(*) FROM leoft_training_plans) <> 1 THEN RAISE EXCEPTION 'legacy plan lost on rollback'; END IF;
          IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leoft_training_exercises' AND column_name='catalog_id') THEN RAISE EXCEPTION 'S3 exercise column survived rollback'; END IF;
        END $$;
        """,
    )
    apply_file(container, "legacy", UPGRADE)


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    network = f"leoft-s3-net-{suffix}"
    database_container = f"leoft-s3-pg-{suffix}"
    try:
        run(["docker", "network", "create", network])
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                database_container,
                "--network",
                network,
                "--network-alias",
                "db",
                "-e",
                f"POSTGRES_USER={DB_USER}",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                IMAGE,
            ]
        )
        wait_ready(database_container)
        create_legacy_schema(database_container)
        validate_upgrade_and_rollback(database_container)

        docker_exec_psql(database_container, "postgres", "CREATE DATABASE runtime;")
        runtime_url = f"postgresql+asyncpg://{DB_USER}@db:5432/runtime"
        create_script = """
import asyncio
from sqlalchemy import text
from db.database import Base, engine
import db.models
async def main():
    async with engine.begin() as connection:
        await connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
asyncio.run(main())
"""
        common = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "-e",
            f"DATABASE_URL={runtime_url}",
            "-e",
            f"LEOFT_S3_TEST_DATABASE_URL={runtime_url}",
            "-v",
            f"{ROOT}:/workspace",
            "-w",
            "/workspace",
            "--entrypoint",
            "python",
            APP_IMAGE,
        ]
        run([*common, "-c", create_script])
        apply_file(database_container, "runtime", UPGRADE)
        result = run(
            [
                *common,
                "-m",
                "unittest",
                "-v",
                "tests.services.test_leoft_generation_postgres",
            ]
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        print("migration_upgrade_downgrade_reupgrade=ok")
        print("postgres_service_integration=ok")
        return 0
    finally:
        run(["docker", "rm", "-f", database_container], check=False)
        run(["docker", "network", "rm", network], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
