#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.meta-schema-isolated.yml"
UPGRADE_SQL = ROOT / "db/migrations/2026_07_17_phase2_multichannel_persistence.sql"
ROLLBACK_SQL = ROOT / "db/migrations/2026_07_17_phase2_multichannel_persistence_rollback.sql"

DB_USER = "agentos_meta_iso"

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
CONN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
CONN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
LEAD_A = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"
LEAD_B = "dddddddd-dddd-4ddd-8ddd-ddddddddddd2"
CONTACT_A = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1"
CONTACT_B = "ffffffff-ffff-4fff-8fff-fffffffffff2"
CONV_A = "99999999-9999-4999-8999-999999999991"
CONV_B = "88888888-8888-4888-8888-888888888882"


LEGACY_SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TYPE tipo_conexao_enum AS ENUM ('EVOLUTION', 'META', 'INSTAGRAM', 'evolution', 'meta', 'instagram');

CREATE TABLE empresas (
    id UUID PRIMARY KEY,
    nome_empresa VARCHAR NOT NULL,
    credenciais_canais JSONB DEFAULT '{{}}'::jsonb
);

CREATE TABLE conexoes (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    tipo tipo_conexao_enum NOT NULL,
    nome_instancia VARCHAR NOT NULL,
    credenciais JSONB,
    status VARCHAR NOT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE
);

CREATE TABLE crm_funis (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome VARCHAR NOT NULL
);

CREATE TABLE crm_etapas (
    id UUID PRIMARY KEY,
    funil_id UUID NOT NULL REFERENCES crm_funis(id) ON DELETE CASCADE,
    nome VARCHAR NOT NULL,
    ordem INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE crm_leads (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    etapa_id UUID NULL REFERENCES crm_etapas(id) ON DELETE SET NULL,
    nome_contato VARCHAR NOT NULL,
    telefone_contato VARCHAR NULL,
    historico_resumo TEXT NULL,
    tags JSONB NOT NULL,
    dados_adicionais JSONB,
    status_atendimento VARCHAR NOT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE,
    ia_ativa BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE mensagens_historico (
    id UUID PRIMARY KEY,
    lead_id UUID NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    conexao_id UUID NULL REFERENCES conexoes(id) ON DELETE SET NULL,
    texto TEXT NOT NULL,
    tipo_mensagem VARCHAR NOT NULL,
    media_url TEXT NULL,
    from_me BOOLEAN NOT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE
);

INSERT INTO empresas (id, nome_empresa) VALUES
('{A}', 'Tenant A'),
('{B}', 'Tenant B');

INSERT INTO conexoes (id, empresa_id, tipo, nome_instancia, credenciais, status, criado_em) VALUES
('{CONN_A}', '{A}', 'evolution', 'conn-a', '{{}}'::jsonb, 'ativo', now()),
('{CONN_B}', '{B}', 'meta', 'conn-b', '{{}}'::jsonb, 'ativo', now());

INSERT INTO crm_leads (id, empresa_id, nome_contato, telefone_contato, tags, dados_adicionais, status_atendimento, criado_em, ia_ativa) VALUES
('{LEAD_A}', '{A}', 'Lead A', NULL, '[]'::jsonb, '{{}}'::jsonb, 'aberto', now(), true),
('{LEAD_B}', '{B}', 'Lead B', NULL, '[]'::jsonb, '{{}}'::jsonb, 'aberto', now(), true);

INSERT INTO mensagens_historico (id, lead_id, conexao_id, texto, tipo_mensagem, media_url, from_me, criado_em) VALUES
('12121212-1212-4121-8121-121212121211', '{LEAD_A}', '{CONN_A}', 'synthetic message A', 'text', NULL, false, now()),
('34343434-3434-4343-8343-343434343432', '{LEAD_B}', '{CONN_B}', 'synthetic message B', 'text', NULL, false, now());
"""


REQUIRED_CONSTRAINTS = (
    "uq_conexoes_empresa_id_id",
    "uq_crm_leads_empresa_id_id",
    "uq_channel_contacts_empresa_id_id",
    "uq_channel_conversations_empresa_id_id",
    "fk_channel_contacts_empresa_conexao",
    "fk_channel_contacts_empresa_lead",
    "fk_channel_conversations_empresa_conexao",
    "fk_channel_conversations_empresa_contact",
    "fk_channel_conversations_empresa_lead",
    "fk_mensagens_historico_empresa_lead",
    "fk_mensagens_historico_empresa_conexao",
    "fk_mensagens_historico_empresa_conversation",
)


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True, log: pathlib.Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=ROOT,
        capture_output=True,
        env=env,
    )
    duration = time.monotonic() - started
    rendered = "$ " + " ".join(shlex.quote(part) for part in cmd) + f"\n# exit={proc.returncode} duration={duration:.2f}s\n"
    if proc.stdout:
        rendered += proc.stdout
    if proc.stderr:
        rendered += proc.stderr
    rendered += "\n"
    if log:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(rendered)
    if check and proc.returncode != 0:
        raise RuntimeError(rendered)
    return proc


def compose(project: str, args: list[str], *, log: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return run(["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args], log=log, env=env)


def psql(project: str, database: str, sql: str, *, log: pathlib.Path, env: dict[str, str], single_transaction: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    args = ["exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1"]
    if single_transaction:
        args.append("-1")
    args.extend(["-U", DB_USER, "-d", database])
    return compose(project, args, log=log, env=env) if sql == "" else run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        input_text=sql,
        check=check,
        log=log,
        env=env,
    )


def reset_database(project: str, name: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    admin_sql = f"DROP DATABASE IF EXISTS {name};\nCREATE DATABASE {name};\n"
    psql(project, "postgres", admin_sql, log=log, env=env)


def apply_file(project: str, database: str, path: pathlib.Path, *, log: pathlib.Path, env: dict[str, str]) -> None:
    psql(project, database, path.read_text(encoding="utf-8"), log=log, env=env, single_transaction=True)


def verify_constraints(project: str, database: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    values = ", ".join(f"('{name}')" for name in REQUIRED_CONSTRAINTS)
    sql = f"""
WITH required(name) AS (VALUES {values})
SELECT required.name
FROM required
LEFT JOIN pg_constraint c ON c.conname = required.name
WHERE c.oid IS NULL
ORDER BY required.name;
"""
    proc = run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "db",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-t",
            "-A",
            "-U",
            DB_USER,
            "-d",
            database,
        ],
        input_text=sql,
        log=log,
        env=env,
    )
    missing = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if missing:
        raise AssertionError(f"missing constraints in {database}: {missing}")


def verify_counts(project: str, database: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    sql = """
WITH violations AS (
    SELECT count(*) AS n FROM mensagens_historico WHERE empresa_id IS NULL
    UNION ALL SELECT count(*) FROM mensagens_historico mh JOIN crm_leads cl ON cl.id = mh.lead_id WHERE mh.empresa_id IS DISTINCT FROM cl.empresa_id
    UNION ALL SELECT count(*) FROM channel_contacts cc JOIN conexoes c ON c.id = cc.conexao_id WHERE cc.empresa_id IS DISTINCT FROM c.empresa_id
    UNION ALL SELECT count(*) FROM channel_conversations cv JOIN channel_contacts cc ON cc.id = cv.contact_id WHERE cv.empresa_id IS DISTINCT FROM cc.empresa_id
)
SELECT COALESCE(sum(n), 0) AS violations FROM violations;
"""
    proc = psql(project, database, sql, log=log, env=env)
    numbers = [int(match.group(1)) for match in re.finditer(r"^\s*(\d+)\s*$", proc.stdout, re.MULTILINE)]
    if not numbers or numbers[-1] != 0:
        raise AssertionError(f"unexpected non-zero tenant checks in {database}: {proc.stdout}")


def expect_fail(project: str, database: str, sql: str, label: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    proc = psql(project, database, sql, log=log, env=env, check=False)
    if proc.returncode == 0:
        raise AssertionError(f"negative test unexpectedly succeeded: {label}")
    if "violates foreign key constraint" not in (proc.stderr + proc.stdout):
        raise AssertionError(f"negative test failed for unexpected reason: {label}")


def insert_valid_multichannel(project: str, database: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    sql = f"""
INSERT INTO channel_contacts (id, empresa_id, conexao_id, lead_id, channel, provider, external_contact_id)
VALUES
('{CONTACT_A}', '{A}', '{CONN_A}', '{LEAD_A}', 'whatsapp', 'evolution_legacy', 'contact-a')
ON CONFLICT DO NOTHING;

INSERT INTO channel_contacts (id, empresa_id, conexao_id, lead_id, channel, provider, external_contact_id)
VALUES
('{CONTACT_B}', '{B}', '{CONN_B}', '{LEAD_B}', 'whatsapp', 'meta_whatsapp', 'contact-b')
ON CONFLICT DO NOTHING;

INSERT INTO channel_conversations (id, empresa_id, conexao_id, contact_id, lead_id, channel, provider, conversation_key, external_conversation_id)
VALUES
('{CONV_A}', '{A}', '{CONN_A}', '{CONTACT_A}', '{LEAD_A}', 'whatsapp', 'evolution_legacy', 'conv-a', 'conv-a-ext')
ON CONFLICT DO NOTHING;

INSERT INTO channel_conversations (id, empresa_id, conexao_id, contact_id, lead_id, channel, provider, conversation_key, external_conversation_id)
VALUES
('{CONV_B}', '{B}', '{CONN_B}', '{CONTACT_B}', '{LEAD_B}', 'whatsapp', 'meta_whatsapp', 'conv-b', 'conv-b-ext')
ON CONFLICT DO NOTHING;
"""
    psql(project, database, sql, log=log, env=env)


def run_negative_tests(project: str, database: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    insert_valid_multichannel(project, database, log=log, env=env)
    expect_fail(
        project,
        database,
        f"INSERT INTO channel_contacts (id, empresa_id, conexao_id, channel, provider, external_contact_id) VALUES (gen_random_uuid(), '{A}', '{CONN_B}', 'whatsapp', 'meta_whatsapp', 'bad-conn');",
        "contact cannot reference connection from another tenant",
        log=log,
        env=env,
    )
    expect_fail(
        project,
        database,
        f"INSERT INTO channel_contacts (id, empresa_id, conexao_id, lead_id, channel, provider, external_contact_id) VALUES (gen_random_uuid(), '{A}', '{CONN_A}', '{LEAD_B}', 'whatsapp', 'evolution_legacy', 'bad-lead');",
        "contact cannot reference lead from another tenant",
        log=log,
        env=env,
    )
    expect_fail(
        project,
        database,
        f"INSERT INTO channel_conversations (id, empresa_id, conexao_id, contact_id, channel, provider, conversation_key, external_conversation_id) VALUES (gen_random_uuid(), '{A}', '{CONN_A}', '{CONTACT_B}', 'whatsapp', 'evolution_legacy', 'bad-contact', 'bad-contact');",
        "conversation cannot reference contact from another tenant",
        log=log,
        env=env,
    )
    expect_fail(
        project,
        database,
        f"INSERT INTO mensagens_historico (id, empresa_id, lead_id, conexao_id, texto, tipo_mensagem, from_me) VALUES (gen_random_uuid(), '{A}', '{LEAD_B}', '{CONN_A}', 'bad lead', 'text', false);",
        "message cannot reference lead from another tenant",
        log=log,
        env=env,
    )


def scenario_legacy(project: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    reset_database(project, "agentos_legacy_iso", log=log, env=env)
    psql(project, "agentos_legacy_iso", LEGACY_SCHEMA_SQL, log=log, env=env, single_transaction=True)
    apply_file(project, "agentos_legacy_iso", UPGRADE_SQL, log=log, env=env)
    verify_constraints(project, "agentos_legacy_iso", log=log, env=env)
    verify_counts(project, "agentos_legacy_iso", log=log, env=env)
    run_negative_tests(project, "agentos_legacy_iso", log=log, env=env)
    apply_file(project, "agentos_legacy_iso", ROLLBACK_SQL, log=log, env=env)
    psql(
        project,
        "agentos_legacy_iso",
        "SELECT to_regclass('public.channel_contacts') AS contacts, to_regclass('public.channel_conversations') AS conversations;",
        log=log,
        env=env,
    )
    apply_file(project, "agentos_legacy_iso", UPGRADE_SQL, log=log, env=env)
    verify_constraints(project, "agentos_legacy_iso", log=log, env=env)
    verify_counts(project, "agentos_legacy_iso", log=log, env=env)
    run_negative_tests(project, "agentos_legacy_iso", log=log, env=env)


def scenario_partial(project: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    reset_database(project, "agentos_partial_iso", log=log, env=env)
    psql(project, "agentos_partial_iso", LEGACY_SCHEMA_SQL, log=log, env=env, single_transaction=True)
    partial_sql = """
ALTER TABLE conexoes ADD CONSTRAINT uq_conexoes_empresa_id_id UNIQUE (empresa_id, id);
CREATE TABLE channel_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL,
    conexao_id UUID NOT NULL,
    channel VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    external_contact_id VARCHAR NOT NULL
);
"""
    psql(project, "agentos_partial_iso", partial_sql, log=log, env=env, single_transaction=True)
    apply_file(project, "agentos_partial_iso", UPGRADE_SQL, log=log, env=env)
    verify_constraints(project, "agentos_partial_iso", log=log, env=env)
    verify_counts(project, "agentos_partial_iso", log=log, env=env)
    run_negative_tests(project, "agentos_partial_iso", log=log, env=env)


def scenario_backend(project: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    reset_database(project, "agentos_backend_iso", log=log, env=env)
    compose(project, ["up", "-d", "--wait", "--build", "backend"], log=log, env=env)
    deadline = time.monotonic() + 120
    healthy = False
    while time.monotonic() < deadline:
        ps = compose(project, ["ps", "backend"], log=log, env=env)
        if "healthy" in ps.stdout:
            healthy = True
            break
        time.sleep(5)
    logs = compose(project, ["logs", "--tail=200", "backend"], log=log, env=env)
    if "InvalidForeignKeyError" in logs.stdout or "InvalidForeignKeyError" in logs.stderr:
        raise AssertionError("backend isolated logs contain InvalidForeignKeyError")
    if not healthy:
        raise AssertionError("backend isolated did not become healthy")


def scenario_new(project: str, *, log: pathlib.Path, env: dict[str, str]) -> None:
    reset_database(project, "agentos_new_iso", log=log, env=env)
    compose(
        project,
        [
            "run",
            "--rm",
            "-e",
            "DATABASE_URL=postgresql+asyncpg://agentos_meta_iso:agentos_meta_iso_pw@db:5432/agentos_new_iso",
            "backend",
            "python",
            "init_db.py",
        ],
        log=log,
        env=env,
    )
    apply_file(project, "agentos_new_iso", UPGRADE_SQL, log=log, env=env)
    verify_constraints(project, "agentos_new_iso", log=log, env=env)
    seed_sql = f"""
INSERT INTO empresas (
    id,
    nome_empresa,
    condutor_ativo,
    limite_certeza,
    limite_duvida,
    max_agentes_desempate,
    disparo_delay_min,
    disparo_delay_max
) VALUES
('{A}', 'Tenant A', false, 0.65, 0.45, 3, 3, 7),
('{B}', 'Tenant B', false, 0.65, 0.45, 3, 3, 7)
ON CONFLICT DO NOTHING;
INSERT INTO conexoes (id, empresa_id, tipo, nome_instancia, status) VALUES
('{CONN_A}', '{A}', 'EVOLUTION', 'conn-a', 'ativo'),
('{CONN_B}', '{B}', 'META', 'conn-b', 'ativo')
ON CONFLICT DO NOTHING;
INSERT INTO crm_leads (id, empresa_id, nome_contato, tags, status_atendimento) VALUES
('{LEAD_A}', '{A}', 'Lead A', '[]'::jsonb, 'aberto'),
('{LEAD_B}', '{B}', 'Lead B', '[]'::jsonb, 'aberto')
ON CONFLICT DO NOTHING;
INSERT INTO mensagens_historico (id, empresa_id, lead_id, conexao_id, texto, tipo_mensagem, from_me) VALUES
('12121212-1212-4121-8121-121212121211', '{A}', '{LEAD_A}', '{CONN_A}', 'synthetic message A', 'text', false),
('34343434-3434-4343-8343-343434343432', '{B}', '{LEAD_B}', '{CONN_B}', 'synthetic message B', 'text', false)
ON CONFLICT DO NOTHING;
"""
    psql(project, "agentos_new_iso", seed_sql, log=log, env=env)
    verify_counts(project, "agentos_new_iso", log=log, env=env)
    run_negative_tests(project, "agentos_new_iso", log=log, env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--db-port", default="55432")
    parser.add_argument("--backend-port", default="18000")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    report_dir = pathlib.Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    log = report_dir / "postgres_isolado_runner.log"
    if log.exists():
        log.unlink()

    env = os.environ.copy()
    env["AGENTOS_META_ISO_DB_PORT"] = args.db_port
    env["AGENTOS_META_ISO_BACKEND_PORT"] = args.backend_port

    try:
        compose(args.project, ["up", "-d", "--wait", "db", "redis"], log=log, env=env)
        scenario_legacy(args.project, log=log, env=env)
        scenario_partial(args.project, log=log, env=env)
        scenario_new(args.project, log=log, env=env)
        scenario_backend(args.project, log=log, env=env)
        (report_dir / "postgres_isolado_status.txt").write_text("OK\n", encoding="utf-8")
        return 0
    except Exception as exc:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\nFAILED: {exc}\n")
        (report_dir / "postgres_isolado_status.txt").write_text(f"FAIL: {exc}\n", encoding="utf-8")
        return 1
    finally:
        if not args.keep:
            compose(args.project, ["logs", "--no-color"], log=log, env=env)
            compose(args.project, ["down", "-v", "--remove-orphans"], log=log, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
