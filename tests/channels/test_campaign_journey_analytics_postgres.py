from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.campaign_central_dispatch_service import campaign_results_analytics
from app.services import campaign_journey_analytics as journey
from app.services.campaign_journey_analytics import _OUTCOME_SQL


@pytest.mark.asyncio
async def test_outcome_sql_uses_synthetic_postgres_facts_and_tenant_scope():
    database_url = os.getenv("A10_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("A10_TEST_DATABASE_URL must point to a disposable local PostgreSQL database")
    if make_url(database_url).host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("A10 integration tests only allow loopback PostgreSQL; production URLs are refused")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    tenant_id, other_tenant = uuid.uuid4(), uuid.uuid4()
    campaign_id = uuid.uuid4()
    recipient_id, other_recipient, cancelled_recipient, fallback_recipient, delivered_fallback_recipient, untimed_recipient, future_recipient, future_update_recipient = [uuid.uuid4() for _ in range(8)]
    lead_id, fallback_lead_id, delivered_fallback_lead_id, contact_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    connection_id = uuid.uuid4()
    sent_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    fallback_sent_at = datetime(2026, 10, 1, 2, tzinfo=timezone.utc)
    fallback_origin_at = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
    delivered_fallback_sent_at = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    delivered_fallback_origin_at = datetime(2026, 10, 1, 3, tzinfo=timezone.utc)
    observed_at = datetime(2026, 10, 20, tzinfo=timezone.utc)

    ddl = [
        "CREATE TEMP TABLE campaign_recipients (id uuid, empresa_id uuid, campanha_id uuid, lead_id uuid, contact_id uuid, conversation_id uuid, conexao_id uuid, provider text, channel text, external_message_id text, status text, error_code text, sent_at timestamp, created_at timestamp, delivered_at timestamp, read_at timestamp, failed_at timestamp, cancelled_at timestamp, updated_at timestamp)",
        "CREATE TEMP TABLE message_outbox (id uuid, empresa_id uuid, campaign_recipient_id uuid, provider text, conexao_id uuid, channel text, attempts integer, updated_at timestamp)",
        "CREATE TEMP TABLE message_attempts (id uuid, empresa_id uuid, outbox_id uuid, provider text, conexao_id uuid, result_status text, external_message_id text, finished_at timestamp, started_at timestamp)",
        "CREATE TEMP TABLE mensagens_historico (id uuid, empresa_id uuid, channel_conversation_id uuid, direction text, from_me boolean, tipo_mensagem text, metadata_json jsonb, criado_em timestamp)",
        "CREATE TEMP TABLE attribution_touches (id uuid, empresa_id uuid, campaign_recipient_id uuid, outbound_campaign_id uuid, event_kind text, occurred_at timestamptz, lead_id uuid, contact_id uuid, conversation_id uuid)",
        "CREATE TEMP TABLE commercial_events (id uuid, empresa_id uuid, lead_id uuid, conversation_id uuid, event_name text, funnel_stage_id text, revenue_verified boolean, occurred_at timestamptz)",
        "CREATE TEMP TABLE communication_consents (id uuid, empresa_id uuid, contact_id uuid, conexao_id uuid, provider text, channel text, status text)",
        "CREATE TEMP TABLE message_status_events (id uuid, empresa_id uuid, conexao_id uuid, provider text, channel text, external_message_id text, status text, occurred_at timestamp, created_at timestamp)",
    ]
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                for statement in ddl:
                    await connection.exec_driver_sql(statement)
                await connection.execute(text("""
                    INSERT INTO campaign_recipients VALUES
                    (:id, :tenant, :campaign, :lead, :contact, :conversation, :connection, 'meta', 'whatsapp', 'wamid-a10-test', 'delivered', NULL, :sent, :sent, NULL, NULL, NULL, NULL, :future),
                    (:other_id, :other_tenant, :campaign, NULL, NULL, NULL, :connection, 'meta', 'whatsapp', 'wamid-other-tenant', 'sent', NULL, :sent, :sent, NULL, NULL, NULL, NULL, :sent),
                    (:cancelled_id, :tenant, :campaign, NULL, :cancelled_contact, NULL, :connection, 'meta', 'whatsapp', NULL, 'cancelled', 'communication_consent_block', NULL, :sent, NULL, NULL, NULL, :sent, :sent),
                    (:fallback_id, :tenant, :campaign, :fallback_lead, NULL, NULL, :connection, 'meta', 'whatsapp', NULL, 'queued', NULL, NULL, :sent, NULL, NULL, NULL, NULL, :sent),
                    (:delivered_fallback_id, :tenant, :campaign, :delivered_fallback_lead, NULL, NULL, :connection, 'meta', 'whatsapp', 'wamid-delivered-fallback', 'delivered', NULL, NULL, :sent, :delivered_fallback_at, NULL, NULL, NULL, :delivered_fallback_at),
                    (:untimed_id, :tenant, :campaign, NULL, NULL, NULL, :connection, 'meta', 'whatsapp', 'wamid-untimed', 'sent', NULL, NULL, :sent, NULL, NULL, NULL, NULL, :sent),
                    (:future_id, :tenant, :campaign, NULL, NULL, NULL, :connection, 'meta', 'whatsapp', 'wamid-future', 'sent', NULL, :future, :future, NULL, NULL, NULL, NULL, :future),
                    (:future_update_id, :tenant, :campaign, NULL, NULL, NULL, :connection, 'meta', 'whatsapp', NULL, 'failed', 'future_failure', NULL, :sent, NULL, NULL, :future, NULL, :future)
                """), {
                    "id": recipient_id, "other_id": other_recipient, "tenant": tenant_id,
                    "other_tenant": other_tenant, "campaign": campaign_id, "lead": lead_id,
                    "fallback_lead": fallback_lead_id, "delivered_fallback_lead": delivered_fallback_lead_id,
                    "contact": contact_id, "conversation": conversation_id,
                    "cancelled_id": cancelled_recipient, "cancelled_contact": uuid.uuid4(),
                    "connection": connection_id, "sent": sent_at.replace(tzinfo=None),
                    "fallback_id": fallback_recipient, "untimed_id": untimed_recipient,
                    "delivered_fallback_id": delivered_fallback_recipient,
                    "future_id": future_recipient, "future_update_id": future_update_recipient,
                    "future": datetime(2026, 10, 21),
                    "delivered_fallback_at": delivered_fallback_sent_at.replace(tzinfo=None),
                })
                outbox_id = uuid.uuid4()
                fallback_outbox_id = uuid.uuid4()
                untimed_outbox_id = uuid.uuid4()
                future_outbox_id = uuid.uuid4()
                await connection.execute(text("INSERT INTO message_outbox VALUES (:id, :tenant, :recipient, 'meta', :connection, 'whatsapp', 1, :sent), (:fallback_outbox, :tenant, :fallback_recipient, 'meta', :connection, 'whatsapp', 1, :sent), (:untimed_outbox, :tenant, :untimed_recipient, 'meta', :connection, 'whatsapp', 1, :future), (:future_outbox, :tenant, :future_update_recipient, 'meta', :connection, 'whatsapp', 1, :future)"),
                                         {"id": outbox_id, "tenant": tenant_id, "recipient": recipient_id,
                                          "fallback_outbox": fallback_outbox_id, "fallback_recipient": fallback_recipient,
                                          "untimed_outbox": untimed_outbox_id, "untimed_recipient": untimed_recipient,
                                          "future_outbox": future_outbox_id, "future_update_recipient": future_update_recipient,
                                          "connection": connection_id, "sent": sent_at.replace(tzinfo=None),
                                          "future": datetime(2026, 10, 21)})
                await connection.execute(text("INSERT INTO message_attempts VALUES (:id, :tenant, :outbox, 'meta', :connection, 'accepted', 'wamid-a10-test', :finished, :started), (:fallback_attempt, :tenant, :fallback_outbox, 'meta', :connection, 'accepted', 'wamid-fallback', :fallback_finished, :started), (:future_attempt, :tenant, :future_outbox, 'meta', :connection, 'started', NULL, NULL, :future)"),
                                         {"id": uuid.uuid4(), "tenant": tenant_id, "outbox": outbox_id,
                                          "fallback_attempt": uuid.uuid4(), "fallback_outbox": fallback_outbox_id,
                                          "future_attempt": uuid.uuid4(), "future_outbox": future_outbox_id,
                                          "connection": connection_id, "finished": sent_at.replace(tzinfo=None),
                                          "fallback_finished": fallback_sent_at.replace(tzinfo=None),
                                          "started": sent_at.replace(tzinfo=None), "future": datetime(2026, 10, 21)})
                await connection.execute(text("""
                    INSERT INTO mensagens_historico VALUES
                    (:prior_id, :tenant, :conversation, 'inbound', false, 'text', '{}'::jsonb, :prior),
                    (:reply_id, :tenant, :conversation, 'inbound', false, 'text', '{}'::jsonb, :reply)
                """), {"prior_id": uuid.uuid4(), "reply_id": uuid.uuid4(), "tenant": tenant_id,
                       "conversation": conversation_id,
                       "prior": datetime(2026, 9, 20), "reply": datetime(2026, 10, 2)})
                await connection.execute(text("""
                    INSERT INTO attribution_touches VALUES
                    (:origin_id, :tenant, NULL, NULL, 'ctwa_referral', :origin_at, :lead, :contact, :conversation),
                    (:fallback_origin_id, :tenant, NULL, NULL, 'tracked_link_click', :fallback_origin_at, :fallback_lead, NULL, NULL),
                    (:delivered_fallback_origin_id, :tenant, NULL, NULL, 'tracked_link_click', :delivered_fallback_origin_at, :delivered_fallback_lead, NULL, NULL),
                    (:sent_touch, :tenant, :recipient, :campaign, 'campaign_sent', :sent_at, :lead, :contact, :conversation),
                    (:reply_touch, :tenant, :recipient, :campaign, 'campaign_reply', :reply_at, :lead, :contact, :conversation)
                """), {"origin_id": uuid.uuid4(), "fallback_origin_id": uuid.uuid4(), "delivered_fallback_origin_id": uuid.uuid4(), "sent_touch": uuid.uuid4(), "reply_touch": uuid.uuid4(),
                       "tenant": tenant_id, "recipient": recipient_id, "campaign": campaign_id,
                       "origin_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "sent_at": sent_at,
                       "fallback_origin_at": fallback_origin_at,
                       "delivered_fallback_origin_at": delivered_fallback_origin_at,
                       "reply_at": datetime(2026, 10, 2, tzinfo=timezone.utc), "lead": lead_id,
                       "fallback_lead": fallback_lead_id,
                       "delivered_fallback_lead": delivered_fallback_lead_id,
                       "contact": contact_id, "conversation": conversation_id})
                await connection.execute(text("""
                    INSERT INTO commercial_events VALUES
                    (:before_id, :tenant, :lead, :conversation, 'QualifiedLead', 'qualified', false, :before),
                    (:advance_id, :tenant, :lead, :conversation, 'QualifiedLead', 'customer', false, :advance),
                    (:purchase_id, :tenant, :lead, :conversation, 'Purchase', NULL, true, :purchase)
                """), {"before_id": uuid.uuid4(), "advance_id": uuid.uuid4(), "purchase_id": uuid.uuid4(),
                       "tenant": tenant_id, "lead": lead_id, "conversation": conversation_id,
                       "before": datetime(2026, 9, 30, tzinfo=timezone.utc),
                       "advance": datetime(2026, 10, 3, tzinfo=timezone.utc),
                       "purchase": datetime(2026, 10, 4, tzinfo=timezone.utc)})
                await connection.execute(text("INSERT INTO communication_consents VALUES (:id, :tenant, :contact, :connection, 'meta', 'whatsapp', 'opted_out')"),
                                         {"id": uuid.uuid4(), "tenant": tenant_id, "contact": contact_id,
                                          "connection": connection_id})
                await connection.execute(text("""
                    INSERT INTO message_status_events VALUES
                    (:sent_id, :tenant, :connection, 'meta', 'whatsapp', 'wamid-a10-test', 'sent', :sent, :sent),
                    (:wrong_connection_id, :tenant, :other_connection, 'meta', 'whatsapp', 'wamid-a10-test', 'delivered', :sent, :sent),
                    (:future_status_id, :tenant, :connection, 'meta', 'whatsapp', 'wamid-a10-test', 'delivered', :future, :future)
                """), {"sent_id": uuid.uuid4(), "wrong_connection_id": uuid.uuid4(),
                       "future_status_id": uuid.uuid4(), "tenant": tenant_id,
                       "connection": connection_id, "other_connection": uuid.uuid4(),
                       "sent": sent_at.replace(tzinfo=None), "future": datetime(2026, 10, 21)})

                result = await connection.execute(_OUTCOME_SQL, {
                    "tenant": tenant_id, "campaign_ids": [campaign_id],
                    "period_start": datetime(2026, 9, 1), "period_end": datetime(2026, 11, 1),
                    "observed_at": observed_at, "response_window_days": 7, "reactivation_days": 7,
                    "stage_window_days": 30, "purchase_window_days": 30,
                    "recipient_limit": 10_000,
                    "stages_json": '[{"key":"qualified","order":1},{"key":"customer","order":2}]',
                })
                rows = result.mappings().all()
                facts = {row["origin_segment"]: dict(row) for row in rows}
                assert set(facts) == {"ctwa_referral", "tracked_link_click", "unknown"}
                assert sum(fact["sampled_recipients"] for fact in facts.values()) == 6
                assert sum(fact["attempted"] for fact in facts.values()) == 3
                assert facts["ctwa_referral"]["replied"] == 1
                assert facts["ctwa_referral"]["reactivated"] == 1
                assert facts["ctwa_referral"]["stage_advanced"] == 1
                assert facts["ctwa_referral"]["verified_purchase"] == 1
                assert facts["ctwa_referral"]["campaign_sent_touches"] == 1
                assert facts["ctwa_referral"]["campaign_reply_touches"] == 1
                assert facts["ctwa_referral"]["current_opt_out"] == 1
                assert facts["ctwa_referral"]["provider_status_observed"] == 1
                assert facts["ctwa_referral"]["provider_delivery_observed"] == 0
                assert facts["ctwa_referral"]["provider_read_observed"] == 0
                assert sum(fact["sent_recipients"] for fact in facts.values()) == 4
                assert sum(fact["accepted_without_timestamp"] for fact in facts.values()) == 1
                assert facts["tracked_link_click"]["sampled_recipients"] == 2
                assert facts["tracked_link_click"]["sent_recipients"] == 2
                assert facts["unknown"]["cancelled"] == 1
                assert facts["unknown"]["failed"] == 0
                assert facts["unknown"]["unknown_status"] == 1
                assert facts["unknown"]["consent_blocked"] == 1
                session = AsyncSession(bind=connection)
                delivery = await campaign_results_analytics(
                    session, empresa_id=tenant_id,
                    period_start=datetime(2026, 9, 1), period_end=datetime(2026, 11, 1),
                    include_timeline=False, include_segments=True,
                    observed_at=observed_at.replace(tzinfo=None),
                    recipient_limit=10_000,
                )
                assert delivery["metrics"]["audience_total"] == 6
                assert delivery["metrics"]["meta_accepted"] == 4
                assert delivery["metrics"]["delivered"] == 1
                assert delivery["metrics"]["read"] == 0
                delivery_segments = {segment["segment"]: segment for segment in delivery["segments"]}
                assert delivery_segments["tracked_link_click"]["audience_total"] == 2
                assert delivery_segments["tracked_link_click"]["meta_accepted"] == 2
                assert delivery_segments["tracked_link_click"]["delivered"] == 1
                unbounded_delivery = await campaign_results_analytics(
                    session, empresa_id=tenant_id,
                    period_start=datetime(2026, 9, 1), period_end=datetime(2026, 11, 1),
                    include_timeline=False, recipient_limit=10_000,
                )
                assert unbounded_delivery["metrics"]["audience_total"] == 7
                with patch.object(journey, "_selected_campaigns", new=AsyncMock(return_value=[(campaign_id, "Campanha sintética")])):
                    with patch.object(journey, "load_funnel_configuration", new=AsyncMock(return_value=[])):
                        details = await journey.recipients(
                            session, empresa_id=tenant_id, campaign_id=str(campaign_id),
                            start=datetime(2026, 9, 1, tzinfo=timezone.utc),
                            end=observed_at, observed_at=observed_at, now=observed_at, limit=100,
                        )
                detail_items = {str(item["recipient_id"]): item for item in details["items"]}
                assert len(detail_items) == 6
                assert detail_items[str(fallback_recipient)]["sent_at"] == fallback_sent_at.replace(tzinfo=None)
                assert detail_items[str(delivered_fallback_recipient)]["sent_at"] == delivered_fallback_sent_at.replace(tzinfo=None)
                assert detail_items[str(untimed_recipient)]["is_accepted"] is True
                assert detail_items[str(untimed_recipient)]["acceptance_time_unknown"] is True
                assert detail_items[str(recipient_id)]["status"] == "sent"
                assert detail_items[str(future_update_recipient)]["status"] == "unknown"
                assert detail_items[str(future_update_recipient)]["is_accepted"] is False
                await session.close()
                await transaction.rollback()
            except BaseException:
                if transaction.is_active:
                    await transaction.rollback()
                raise
    finally:
        await engine.dispose()
