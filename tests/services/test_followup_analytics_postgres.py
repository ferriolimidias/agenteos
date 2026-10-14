"""PostgreSQL query integration; only a disposable a09_test database is accepted."""
import os
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.followup_analytics import dashboard
from db.models import (
    CanonicalCommercialEvent, ChannelContact, ChannelConversation, ConfigFollowUp,
    Conexao, Empresa, FollowupExecution, LeadFollowUpLog, MensagemHistorico,
    MessageStatusEvent, CRMLead,
)


URL = os.getenv("FOLLOWUP_ANALYTICS_TEST_DATABASE_URL", "")
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


DDL = (
    "CREATE TABLE IF NOT EXISTS empresas (id uuid PRIMARY KEY, nome_empresa text, credenciais_canais jsonb)",
    "CREATE TABLE IF NOT EXISTS conexoes (id uuid PRIMARY KEY, empresa_id uuid NOT NULL)",
    "CREATE TABLE IF NOT EXISTS crm_leads (id uuid PRIMARY KEY, empresa_id uuid NOT NULL)",
    "CREATE TABLE IF NOT EXISTS channel_contacts (id uuid PRIMARY KEY, empresa_id uuid NOT NULL)",
    "CREATE TABLE IF NOT EXISTS channel_conversations (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, lead_id uuid NOT NULL, channel text, provider text, conversation_key text, external_conversation_id text)",
    "CREATE TABLE IF NOT EXISTS config_followups (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, nome text)",
    "CREATE TABLE IF NOT EXISTS followup_executions (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, lead_id uuid NOT NULL, conversation_id uuid NOT NULL, conexao_id uuid NOT NULL, followup_id uuid NOT NULL, channel text, trigger text, policy_snapshot_json jsonb, status text, stop_reason text, scheduled_at timestamp, created_at timestamp, processing_started_at timestamp, sent_at timestamp, completed_at timestamp, cancelled_at timestamp, failed_at timestamp, external_message_id text, provider text, idempotency_key text)",
    "CREATE TABLE IF NOT EXISTS message_status_events (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, conexao_id uuid NOT NULL, provider text, channel text, external_message_id text, status text, occurred_at timestamp, created_at timestamp)",
    "CREATE TABLE IF NOT EXISTS mensagens_historico (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, lead_id uuid NOT NULL, channel_conversation_id uuid, direction text, from_me boolean, tipo_mensagem text, metadata_json jsonb, criado_em timestamp, texto text)",
    "CREATE TABLE IF NOT EXISTS commercial_events (id uuid PRIMARY KEY, empresa_id uuid NOT NULL, lead_id uuid NOT NULL, conversation_id uuid, funnel_stage_id text, occurred_at timestamptz, event_name text, revenue_verified boolean, provider text, payment_id text, currency text, value numeric, evidence_json jsonb, metadata_json jsonb)",
    "CREATE TABLE IF NOT EXISTS lead_followup_logs (id uuid PRIMARY KEY, lead_id uuid NOT NULL, config_followup_id uuid NOT NULL, data_envio timestamp, status_envio text)",
)


async def _insert_row(session, table, values):
    columns = list(values)
    placeholders = [f"CAST(:{key} AS jsonb)" if isinstance(values[key], (dict, list)) else f":{key}" for key in columns]
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
    parameters = {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in values.items()}
    await session.execute(text(statement), parameters)


@pytest.mark.asyncio
@pytest.mark.skipif(not URL, reason="isolated FOLLOWUP_ANALYTICS_TEST_DATABASE_URL required")
async def test_postgres_aggregates_persisted_delivery_reply_stage_conversion_and_isolation():
    parsed = make_url(URL)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "a09_test" or parsed.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("refusing follow-up analytics fixtures outside a disposable localhost a09_test database")
    async_url = URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url)
    ids = {key: uuid4() for key in (
        "tenant", "other_tenant", "connection", "lead", "contact", "conversation", "followup", "execution",
        "status", "reply", "reply_before", "stage_before", "stage_after", "purchase", "legacy",
        "other_followup", "other_execution", "other_lead", "other_conversation", "recent_activity", "conflicting_mode_execution",
    )}
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=9)
    tenant = ids["tenant"]
    try:
        async with engine.begin() as connection:
            for statement in DDL:
                await connection.execute(text(statement))
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            spec = {"version": "synthetic-a09-v1", "stages": [
                {"key": "qualification_confirmed", "order": 2},
                {"key": "selection_confirmed", "order": 3},
            ]}
            await _insert_row(session, "empresas", {"id": tenant, "nome_empresa": "Synthetic A09 tenant", "credenciais_canais": {"monitor_funnel_spec": spec}})
            await _insert_row(session, "empresas", {"id": ids["other_tenant"], "nome_empresa": "Synthetic other tenant", "credenciais_canais": {}})
            await _insert_row(session, "conexoes", {"id": ids["connection"], "empresa_id": tenant})
            await _insert_row(session, "crm_leads", {"id": ids["lead"], "empresa_id": tenant})
            await _insert_row(session, "channel_contacts", {"id": ids["contact"], "empresa_id": tenant})
            await _insert_row(session, "channel_conversations", {
                "id": ids["conversation"], "empresa_id": tenant, "lead_id": ids["lead"], "channel": "whatsapp",
                "provider": "meta_official", "conversation_key": "synthetic-a09-thread", "external_conversation_id": "synthetic-a09-thread",
            })
            await _insert_row(session, "config_followups", {"id": ids["followup"], "empresa_id": tenant, "nome": "Synthetic follow-up"})
            await _insert_row(session, "config_followups", {"id": ids["other_followup"], "empresa_id": ids["other_tenant"], "nome": "Other tenant follow-up"})
            await _insert_row(session, "followup_executions", {
                "id": ids["execution"], "empresa_id": tenant, "followup_id": ids["followup"], "lead_id": ids["lead"],
                "conversation_id": ids["conversation"], "conexao_id": ids["connection"], "provider": "meta_official",
                "channel": "whatsapp", "trigger": "last_agent_message", "policy_snapshot_json": {"mode": "conversation_resume", "followup_delivery_decision": {"eligible": True}},
                "scheduled_at": sent_at, "status": "delivered", "idempotency_key": "synthetic-a09-execution",
                "external_message_id": "synthetic-provider-message", "processing_started_at": sent_at,
                "sent_at": sent_at, "created_at": NOW.replace(tzinfo=None) - timedelta(days=15),
            })
            await _insert_row(session, "followup_executions", {
                "id": ids["other_execution"], "empresa_id": ids["other_tenant"], "followup_id": ids["other_followup"],
                "lead_id": ids["other_lead"], "conversation_id": ids["other_conversation"], "conexao_id": ids["connection"],
                "provider": "synthetic", "channel": "whatsapp", "trigger": "last_agent_message", "policy_snapshot_json": {"mode": "conversation_resume"},
                "scheduled_at": sent_at, "status": "sent", "idempotency_key": "synthetic-a09-other-execution",
                "external_message_id": "synthetic-other-message", "sent_at": sent_at,
                "created_at": NOW.replace(tzinfo=None) - timedelta(days=2),
            })
            await _insert_row(session, "message_status_events", {
                "id": ids["status"], "empresa_id": tenant, "conexao_id": ids["connection"], "provider": "meta_official",
                "channel": "whatsapp", "external_message_id": "synthetic-provider-message", "status": "delivered",
                "occurred_at": sent_at + timedelta(minutes=1), "created_at": sent_at + timedelta(minutes=1),
            })
            await _insert_row(session, "mensagens_historico", {
                "id": ids["reply"], "empresa_id": tenant, "lead_id": ids["lead"], "channel_conversation_id": ids["conversation"],
                "direction": "inbound", "from_me": False, "tipo_mensagem": "text", "metadata_json": {},
                "texto": "synthetic reply", "criado_em": sent_at + timedelta(days=1),
            })
            await _insert_row(session, "mensagens_historico", {
                "id": ids["reply_before"], "empresa_id": tenant, "lead_id": ids["lead"], "channel_conversation_id": ids["conversation"],
                "direction": "inbound", "from_me": False, "tipo_mensagem": "text", "metadata_json": {},
                "texto": "synthetic old inbound", "criado_em": sent_at - timedelta(days=10),
            })
            await _insert_row(session, "mensagens_historico", {
                "id": ids["recent_activity"], "empresa_id": tenant, "lead_id": ids["lead"], "channel_conversation_id": ids["conversation"],
                "direction": "outbound", "from_me": True, "tipo_mensagem": "text", "metadata_json": {},
                "texto": "synthetic recent agent activity", "criado_em": sent_at - timedelta(days=2),
            })
            for event_id, event_name, stage, occurred_at, payment in (
                (ids["stage_before"], "QualifiedLead", "qualification_confirmed", sent_at - timedelta(days=1), False),
                (ids["stage_after"], "AddToCart", "selection_confirmed", sent_at + timedelta(days=2), False),
                (ids["purchase"], "Purchase", None, sent_at + timedelta(days=3), True),
            ):
                values = {
                    "id": event_id, "empresa_id": tenant, "lead_id": ids["lead"], "conversation_id": ids["conversation"],
                    "funnel_stage_id": stage, "occurred_at": occurred_at.replace(tzinfo=timezone.utc), "event_name": event_name,
                    "revenue_verified": payment, "provider": "mercado_pago" if payment else None,
                    "payment_id": "synthetic-payment" if payment else None, "currency": "BRL" if payment else None,
                    "value": 100 if payment else None,
                    "evidence_json": {"provenance": "provider_verified_payment_status", "environment": "production"} if payment else {},
                    "metadata_json": {},
                }
                await _insert_row(session, "commercial_events", values)
            await _insert_row(session, "lead_followup_logs", {
                "id": ids["legacy"], "lead_id": ids["lead"], "config_followup_id": ids["followup"],
                "data_envio": sent_at, "status_envio": "enviado",
            })
            await session.commit()
            result = await dashboard(session, empresa_id=tenant, start=NOW - timedelta(days=30), end=NOW,
                                     observed_at=NOW, response_window_days=7, reactivation_days=7, now=NOW)
            metrics = result["summary"]
            assert metrics["scheduled_executions"]["numerator"] == 1
            assert metrics["eligible_contacts"]["numerator"] == 1
            assert metrics["messages_sent"]["numerator"] == 1
            assert metrics["deliveries_confirmed"]["numerator"] == 1
            assert metrics["responses_observed"]["numerator"] == 1
            assert metrics["reactivations_observed"]["numerator"] == 0
            assert metrics["stage_advances_observed"]["numerator"] == 1
            assert metrics["confirmed_conversions"]["numerator"] == 1
            assert result["legacy_send_logs"]["send_log_rows"] == 1
            await _insert_row(session, "followup_executions", {
                "id": ids["conflicting_mode_execution"], "empresa_id": tenant, "followup_id": ids["followup"], "lead_id": ids["lead"],
                "conversation_id": ids["conversation"], "conexao_id": ids["connection"], "provider": "meta_official",
                "channel": "whatsapp", "trigger": "last_agent_message", "policy_snapshot_json": {"mode": "scheduled_message"},
                "scheduled_at": NOW.replace(tzinfo=None), "status": "scheduled", "idempotency_key": "synthetic-a09-conflicting-mode",
                "created_at": NOW.replace(tzinfo=None) - timedelta(days=2),
            })
            await session.commit()
            scheduled_mode = await dashboard(session, empresa_id=tenant, start=NOW - timedelta(days=30), end=NOW,
                                             observed_at=NOW, mode="scheduled_message", now=NOW)
            resumed_mode = await dashboard(session, empresa_id=tenant, start=NOW - timedelta(days=30), end=NOW,
                                           observed_at=NOW, mode="conversation_resume", now=NOW)
            assert scheduled_mode["summary"]["scheduled_executions"]["numerator"] == 1
            assert resumed_mode["summary"]["scheduled_executions"]["numerator"] == 1
    finally:
        async with engine.begin() as connection:
            for model in (CanonicalCommercialEvent, MessageStatusEvent, MensagemHistorico, FollowupExecution,
                          ConfigFollowUp, ChannelConversation, ChannelContact, CRMLead, Conexao, Empresa):
                tenant_column = model.__table__.c.get("empresa_id")
                if tenant_column is None:
                    tenant_column = model.__table__.c.get("id")
                await connection.execute(delete(model).where(tenant_column.in_((tenant, ids["other_tenant"]))))
            await connection.execute(delete(LeadFollowUpLog).where(LeadFollowUpLog.lead_id == ids["lead"]))
        await engine.dispose()
