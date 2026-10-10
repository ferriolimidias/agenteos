#!/usr/bin/env python3
from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.meta-schema-isolated.yml"
PHASE_A_SQL = ROOT / "db/migrations/2026_07_20_phase_a_operational_foundation.sql"
UPGRADE_SQL = ROOT / "db/migrations/2026_07_21_phase_b_meta_templates.sql"
ROLLBACK_SQL = ROOT / "db/migrations/2026_07_21_phase_b_meta_templates_rollback.sql"
PROJECT = "agentos_fase_b_templates_iso"
DB_USER = "agentos_meta_iso"
DB_NAME = "agentos_fase_b"

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
CONN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
CONN_A2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
CONN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
TPL_A = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1"
USER_A = "12121212-1212-4121-8121-121212121212"

BASE_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TYPE tipo_conexao_enum AS ENUM ('evolution', 'meta', 'instagram');
CREATE TYPE campanha_disparo_status_enum AS ENUM ('PENDENTE', 'EXECUTANDO', 'CONCLUIDO', 'ERRO', 'pendente', 'executando', 'concluido', 'erro');
CREATE TABLE empresas (id UUID PRIMARY KEY, nome_empresa VARCHAR NOT NULL);
CREATE TABLE usuarios (id UUID PRIMARY KEY, empresa_id UUID NULL REFERENCES empresas(id), nome VARCHAR NOT NULL, email VARCHAR NOT NULL, senha_hash VARCHAR NOT NULL, role VARCHAR NOT NULL, ativo BOOLEAN DEFAULT true);
CREATE TABLE conexoes (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  tipo tipo_conexao_enum NOT NULL,
  channel VARCHAR NULL,
  provider VARCHAR NULL,
  nome_instancia VARCHAR NOT NULL,
  credenciais JSONB DEFAULT '{{}}'::jsonb,
  status VARCHAR NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT false,
  external_account_id VARCHAR NULL,
  waba_id VARCHAR NULL,
  phone_number_id VARCHAR NULL,
  instagram_business_account_id VARCHAR NULL,
  page_id VARCHAR NULL,
  display_number VARCHAR NULL,
  metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
  criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
  atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
  CONSTRAINT uq_conexoes_empresa_id_id UNIQUE (empresa_id, id)
);
CREATE TABLE crm_leads (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE, nome_contato VARCHAR NOT NULL, telefone_contato VARCHAR NULL, tags JSONB NOT NULL DEFAULT '[]'::jsonb, status_atendimento VARCHAR NOT NULL DEFAULT 'aberto', criado_em TIMESTAMP DEFAULT now(), CONSTRAINT uq_crm_leads_empresa_id_id UNIQUE (empresa_id, id));
CREATE TABLE templates_mensagem (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), nome VARCHAR NOT NULL, texto_template TEXT NOT NULL, variaveis_esperadas JSONB NOT NULL DEFAULT '[]'::jsonb);
CREATE TABLE campanhas_disparo (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), nome VARCHAR NOT NULL, template_id UUID NULL REFERENCES templates_mensagem(id), tags_alvo JSONB NOT NULL DEFAULT '[]'::jsonb, data_agendamento TIMESTAMP NULL, status campanha_disparo_status_enum NOT NULL DEFAULT 'pendente', criado_em TIMESTAMP DEFAULT now());
CREATE TABLE tags_crm (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), nome VARCHAR NOT NULL, cor VARCHAR NOT NULL DEFAULT '#2563eb', tipo VARCHAR NOT NULL DEFAULT 'comportamento');
CREATE TABLE config_followups (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), nome VARCHAR NOT NULL, tempo_gatilho_minutos INTEGER NOT NULL, objetivo_prompt TEXT NOT NULL, tag_aplicar_final UUID NULL REFERENCES tags_crm(id), ativo BOOLEAN NOT NULL DEFAULT true, criado_em TIMESTAMP DEFAULT now());
CREATE TABLE channel_contacts (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), conexao_id UUID NOT NULL REFERENCES conexoes(id), lead_id UUID NULL REFERENCES crm_leads(id), channel VARCHAR NOT NULL, provider VARCHAR NOT NULL, external_contact_id VARCHAR NOT NULL, normalized_address VARCHAR NULL, metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb, first_seen_at TIMESTAMP DEFAULT now(), last_seen_at TIMESTAMP DEFAULT now(), CONSTRAINT uq_channel_contacts_empresa_id_id UNIQUE (empresa_id, id), CONSTRAINT uq_channel_contacts_identity UNIQUE (empresa_id, channel, provider, conexao_id, external_contact_id), CONSTRAINT fk_channel_contacts_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id), CONSTRAINT fk_channel_contacts_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id));
CREATE TABLE channel_conversations (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id), conexao_id UUID NOT NULL REFERENCES conexoes(id), contact_id UUID NOT NULL REFERENCES channel_contacts(id), lead_id UUID NULL REFERENCES crm_leads(id), channel VARCHAR NOT NULL, provider VARCHAR NOT NULL, conversation_key VARCHAR NOT NULL, external_conversation_id VARCHAR NOT NULL, status VARCHAR NOT NULL DEFAULT 'open', last_message_at TIMESTAMP NULL, last_inbound_at TIMESTAMP NULL, last_outbound_at TIMESTAMP NULL, metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb, criado_em TIMESTAMP DEFAULT now(), atualizado_em TIMESTAMP DEFAULT now(), CONSTRAINT uq_channel_conversations_empresa_id_id UNIQUE (empresa_id, id), CONSTRAINT uq_channel_conversations_identity UNIQUE (empresa_id, channel, provider, conexao_id, conversation_key), CONSTRAINT fk_channel_conversations_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id), CONSTRAINT fk_channel_conversations_empresa_contact FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts(empresa_id, id), CONSTRAINT fk_channel_conversations_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id));
CREATE TABLE mensagens_historico (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), empresa_id UUID NOT NULL REFERENCES empresas(id), lead_id UUID NOT NULL REFERENCES crm_leads(id), conexao_id UUID NULL REFERENCES conexoes(id), channel_conversation_id UUID NULL REFERENCES channel_conversations(id), channel VARCHAR NULL, provider VARCHAR NULL, external_message_id VARCHAR NULL, texto TEXT NOT NULL, tipo_mensagem VARCHAR NOT NULL DEFAULT 'text', from_me BOOLEAN NOT NULL DEFAULT false, criado_em TIMESTAMP DEFAULT now(), CONSTRAINT fk_mensagens_historico_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id), CONSTRAINT fk_mensagens_historico_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id), CONSTRAINT fk_mensagens_historico_empresa_conversation FOREIGN KEY (empresa_id, channel_conversation_id) REFERENCES channel_conversations(empresa_id, id));

INSERT INTO empresas (id, nome_empresa) VALUES ('{A}', 'Tenant A'), ('{B}', 'Tenant B');
INSERT INTO usuarios (id, empresa_id, nome, email, senha_hash, role) VALUES ('{USER_A}', '{A}', 'User A', 'a@example.invalid', 'x', 'admin_empresa');
INSERT INTO conexoes (id, empresa_id, tipo, channel, provider, nome_instancia, status, waba_id) VALUES
('{CONN_A}', '{A}', 'meta', 'whatsapp', 'meta_whatsapp', 'Meta A', 'connected', 'waba-a'),
('{CONN_A2}', '{A}', 'meta', 'whatsapp', 'meta_whatsapp', 'Meta A2', 'connected', 'waba-a2'),
('{CONN_B}', '{B}', 'meta', 'whatsapp', 'meta_whatsapp', 'Meta B', 'connected', 'waba-b');
"""

REQUIRED = ("meta_message_templates", "meta_template_components", "meta_template_sync_runs")


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    proc = subprocess.run(cmd, input=input_text, text=True, cwd=ROOT, capture_output=True, env=os.environ.copy())
    rendered = "$ " + " ".join(shlex.quote(part) for part in cmd) + f"\n# exit={proc.returncode} duration={time.monotonic() - started:.2f}s\n"
    if proc.stdout:
        rendered += proc.stdout
    if proc.stderr:
        rendered += proc.stderr
    if check and proc.returncode != 0:
        raise RuntimeError(rendered)
    return proc


def compose(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args], check=check)


def wait_db_ready(timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        proc = compose(["exec", "-T", "db", "pg_isready", "-h", "127.0.0.1", "-U", DB_USER], check=False)
        if proc.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("isolated PostgreSQL did not become ready")


def psql(sql: str, *, db: str = DB_NAME, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-h", "127.0.0.1", "-U", DB_USER, "-d", db],
        input_text=sql,
        check=check,
    )


def reset_db() -> None:
    psql(f"DROP DATABASE IF EXISTS {DB_NAME};\nCREATE DATABASE {DB_NAME};\n", db="postgres")


def assert_tables_exist() -> None:
    values = ", ".join(f"('{name}')" for name in REQUIRED)
    proc = psql(f"WITH required(name) AS (VALUES {values}) SELECT name FROM required WHERE to_regclass('public.' || name) IS NULL;")
    missing = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if missing:
        raise AssertionError(f"missing phase B tables: {missing}")


def assert_constraints() -> None:
    expected = (
        "uq_meta_templates_remote_id",
        "uq_meta_templates_name_language_waba",
        "uq_meta_template_components_order",
        "uq_meta_template_sync_runs_idempotency",
        "fk_meta_templates_empresa_conexao",
        "fk_meta_template_components_empresa_template",
    )
    values = ", ".join(f"('{name}')" for name in expected)
    proc = psql(f"WITH expected(name) AS (VALUES {values}) SELECT name FROM expected WHERE NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = expected.name);")
    missing = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if missing:
        raise AssertionError(f"missing constraints: {missing}")


def assert_data_rules() -> None:
    sql = f"""
INSERT INTO meta_message_templates (id, empresa_id, conexao_id, waba_id, remote_template_id, name, language, raw_status, normalized_status, raw_category, normalized_category)
VALUES ('{TPL_A}', '{A}', '{CONN_A}', 'waba-a', 'remote-1', 'welcome', 'pt_BR', 'NEW_STATUS', 'unknown', 'NEW_CAT', 'unknown');
INSERT INTO meta_template_components (empresa_id, template_id, component_type, component_order, raw_component_json)
VALUES ('{A}', '{TPL_A}', 'NEW_COMPONENT', 0, '{{"type":"NEW_COMPONENT"}}'::jsonb);
"""
    psql(sql)
    duplicate = psql(
        f"INSERT INTO meta_message_templates (empresa_id, conexao_id, waba_id, remote_template_id, name, language) VALUES ('{A}', '{CONN_A}', 'waba-a', 'remote-1', 'other', 'pt_BR');",
        check=False,
    )
    if duplicate.returncode == 0 or "uq_meta_templates_remote_id" not in duplicate.stderr + duplicate.stdout:
        raise AssertionError("duplicate remote id was not blocked")
    same_remote_other_tenant = psql(
        f"INSERT INTO meta_message_templates (empresa_id, conexao_id, waba_id, remote_template_id, name, language) VALUES ('{B}', '{CONN_B}', 'waba-b', 'remote-1', 'welcome', 'pt_BR');",
        check=False,
    )
    if same_remote_other_tenant.returncode != 0:
        raise AssertionError("same remote id in another tenant should be allowed")
    same_name_other_waba = psql(
        f"INSERT INTO meta_message_templates (empresa_id, conexao_id, waba_id, name, language) VALUES ('{A}', '{CONN_A2}', 'waba-a2', 'welcome', 'pt_BR');",
        check=False,
    )
    if same_name_other_waba.returncode != 0:
        raise AssertionError("same name/language in another WABA should be allowed")
    cross = psql(
        f"INSERT INTO meta_message_templates (empresa_id, conexao_id, waba_id, name, language) VALUES ('{A}', '{CONN_B}', 'waba-b', 'cross', 'pt_BR');",
        check=False,
    )
    if cross.returncode == 0 or "violates foreign key constraint" not in cross.stderr + cross.stdout:
        raise AssertionError("cross-tenant connection was not blocked")


def main() -> int:
    for path in (PHASE_A_SQL, UPGRADE_SQL, ROLLBACK_SQL):
        if not path.exists():
            raise SystemExit(f"missing migration file: {path}")
    compose(["up", "-d", "db"])
    wait_db_ready()
    reset_db()
    psql(BASE_SCHEMA)
    psql(PHASE_A_SQL.read_text(encoding="utf-8"))
    psql(UPGRADE_SQL.read_text(encoding="utf-8"))
    assert_tables_exist()
    assert_constraints()
    psql(UPGRADE_SQL.read_text(encoding="utf-8"))
    assert_tables_exist()
    assert_data_rules()
    psql(ROLLBACK_SQL.read_text(encoding="utf-8"))
    for table in REQUIRED:
        proc = psql(f"SELECT to_regclass('public.{table}');")
        if table in proc.stdout:
            raise AssertionError(f"rollback kept table {table}")
    psql(UPGRADE_SQL.read_text(encoding="utf-8"))
    assert_tables_exist()
    print("FASE_B_META_TEMPLATES_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
