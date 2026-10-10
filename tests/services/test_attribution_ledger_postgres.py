from __future__ import annotations

import os
import json
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from app.services.attribution_backfill import backfill_attribution, reconcile_legacy_attribution
from app.services.attribution_ledger import (
    campaign_attribution_summary,
    clean_referral,
    ctwa_idempotency_key,
    get_attribution_for_conversation,
    get_attribution_for_lead,
    get_first_touch,
    get_last_touch,
    record_checkout_touch,
    record_payment_touch,
    record_touch,
    resolve_meta_ad_source,
)
from app.services.commercial_events import (
    canonical_dispatch_decision, record_commercial_event,
    record_managed_tag_commercial_event,
)
from app.services.commercial_event_backfill import commercial_event_backfill
from app.services.commercial_analytics import performance
from db.models import AttributionTouchResolution, CanonicalCommercialEvent, CRMTagEvent
from app.api.routers import meta_webhook as meta_webhook_router
from app.services.channels.types import Channel, Provider
from db.manual_migrations import _split_sql_statements


TEST_DATABASE_URL = os.getenv("ATTRIBUTION_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "ATTRIBUTION_TEST_DATABASE_URL must point at an isolated PostgreSQL database")
class AttributionLedgerPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = make_url(TEST_DATABASE_URL)
        if parsed.get_backend_name() != "postgresql" or parsed.database != "attribution_test":
            raise RuntimeError("refusing attribution integration test outside isolated attribution_test PostgreSQL")
        self.engine = create_async_engine(TEST_DATABASE_URL)
        async with self.engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS traffic_meta_configs CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS meta_ads_insights_daily CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS commercial_event_meta_links CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS commercial_events CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS attribution_touch_resolutions CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS attribution_touches CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS business_messaging_conversion_mappings CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS meta_conversion_events CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS crm_tag_events CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS tags_crm CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS leoft_sale_fulfillments CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS leoft_payment_events CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS leoft_integration_events CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS campaign_recipients CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS campanhas_disparo CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS mensagens_historico CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS channel_conversations CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS channel_contacts CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS crm_leads CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS conexoes CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS empresas CASCADE"))
            await connection.execute(text("CREATE TABLE empresas (id uuid PRIMARY KEY)"))
            await connection.execute(text("CREATE TABLE traffic_meta_configs (id uuid PRIMARY KEY, empresa_id uuid NOT NULL REFERENCES empresas(id), business_id text, ad_account_id text, page_id text, instagram_account_id text, whatsapp_connection_id uuid, waba_id text, phone_number_id text, dataset_id text, status text NOT NULL DEFAULT 'draft', created_at timestamptz, updated_at timestamptz, UNIQUE(empresa_id))"))
            await connection.execute(text("CREATE TABLE conexoes (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE crm_leads (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, dados_adicionais jsonb NOT NULL DEFAULT '{}'::jsonb, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE tags_crm (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, instrucao_ia text, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE crm_tag_events (id uuid PRIMARY KEY, empresa_id uuid NOT NULL REFERENCES empresas(id), lead_id uuid NOT NULL, tag_id uuid NOT NULL, action text NOT NULL, source text NOT NULL DEFAULT 'test', source_reference_id text, actor_user_id uuid, contact_id uuid, conversation_id uuid, occurred_at timestamptz NOT NULL DEFAULT now(), idempotency_key text NOT NULL, metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz, UNIQUE(empresa_id,id), FOREIGN KEY(empresa_id,lead_id) REFERENCES crm_leads(empresa_id,id), FOREIGN KEY(empresa_id,tag_id) REFERENCES tags_crm(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE meta_conversion_events (id uuid PRIMARY KEY, empresa_id uuid NOT NULL REFERENCES empresas(id), lead_id uuid, tag_id uuid, event_name text NOT NULL, event_id text NOT NULL, reservation_key text, status text NOT NULL DEFAULT 'dry_run', payload_hash text, payload_preview jsonb NOT NULL DEFAULT '{}'::jsonb, response_preview jsonb, error_message text, attempts integer NOT NULL DEFAULT 0, last_attempt_at timestamptz, criado_em timestamptz NOT NULL DEFAULT now(), atualizado_em timestamptz, test_event_code text, source text NOT NULL DEFAULT 'tag', conversion_context text NOT NULL DEFAULT 'current', action_source text, messaging_channel text, credential_source text, waba_id_masked text, dataset_id_masked text, ctwa_present boolean NOT NULL DEFAULT false, trigger_type text, trigger_ref text, trigger_source text, trigger_source_event_id text, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE business_messaging_conversion_mappings (id uuid PRIMARY KEY, empresa_id uuid NOT NULL REFERENCES empresas(id), trigger_type text NOT NULL, trigger_ref text NOT NULL, event_name text NOT NULL, ativo boolean NOT NULL DEFAULT false, send_mode text NOT NULL DEFAULT 'dry_run', default_value double precision, currency text NOT NULL DEFAULT 'BRL', metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, criado_em timestamptz, atualizado_em timestamptz, UNIQUE(empresa_id,trigger_type,trigger_ref))"))
            await connection.execute(text("CREATE TABLE channel_contacts (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE channel_conversations (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, conexao_id uuid, contact_id uuid, lead_id uuid, last_message_at timestamptz, criado_em timestamptz, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE mensagens_historico (id uuid PRIMARY KEY, empresa_id uuid NOT NULL REFERENCES empresas(id), lead_id uuid, conexao_id uuid, channel_conversation_id uuid, channel text, provider text, external_message_id text, direction text, criado_em timestamptz, metadata_json jsonb, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE campanhas_disparo (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE campaign_recipients (empresa_id uuid NOT NULL REFERENCES empresas(id), id uuid NOT NULL, provider text, channel text, sent_at timestamptz, external_message_id text, contact_id uuid, conversation_id uuid, lead_id uuid, conexao_id uuid, campanha_id uuid, status text, UNIQUE(empresa_id,id))"))
            await connection.execute(text("CREATE TABLE leoft_integration_events (id uuid PRIMARY KEY, integration text, request_payload_sanitized jsonb, event_type text, processed_at timestamptz, created_at timestamptz)"))
            await connection.execute(text("CREATE TABLE leoft_sale_fulfillments (company_id uuid, payment_provider text, payment_id text, payment_status text, lead_id uuid, contact_id uuid, order_id text, paid_at timestamptz, occurred_at timestamptz, amount numeric, currency varchar(3))"))
            await connection.execute(text("CREATE TABLE leoft_payment_events (company_id uuid, student_id uuid, contact_id uuid, payment_id text, provider text, payment_status text, environment text, amount numeric, currency varchar(3), order_id text, occurred_at timestamptz)"))
            migration = Path("db/migrations/2026_10_08_001_attribution_ledger.sql").read_text(encoding="utf-8")
            for statement in _split_sql_statements(migration):
                await connection.execute(text(statement))
            commercial_migration = Path("db/migrations/2026_10_08_002_commercial_intelligence.sql").read_text(encoding="utf-8")
            for statement in _split_sql_statements(commercial_migration):
                await connection.execute(text(statement))
            for statement in _split_sql_statements(commercial_migration):
                await connection.execute(text(statement))
            # Reapplying the same versioned SQL is safe and does not duplicate data.
            for statement in _split_sql_statements(migration):
                await connection.execute(text(statement))
            tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
            lead_a, lead_b = uuid.uuid4(), uuid.uuid4()
            connection_a, contact_a, conversation_a = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await connection.execute(text("INSERT INTO empresas(id) VALUES (:a),(:b)"), {"a": tenant_a, "b": tenant_b})
            await connection.execute(text("INSERT INTO crm_leads(empresa_id,id) VALUES (:ta,:la),(:tb,:lb)"), {"ta": tenant_a, "la": lead_a, "tb": tenant_b, "lb": lead_b})
            await connection.execute(text("INSERT INTO conexoes(empresa_id,id) VALUES (:tenant,:id)"), {"tenant": tenant_a, "id": connection_a})
            await connection.execute(text("INSERT INTO channel_contacts(empresa_id,id) VALUES (:tenant,:id)"), {"tenant": tenant_a, "id": contact_a})
            await connection.execute(text("INSERT INTO channel_conversations(empresa_id,id,conexao_id,contact_id,lead_id,last_message_at,criado_em) VALUES (:tenant,:id,:connection,:contact,:lead,now(),now())"), {"tenant": tenant_a, "id": conversation_a, "connection": connection_a, "contact": contact_a, "lead": lead_a})
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.tenant_a, self.tenant_b = tenant_a, tenant_b
        self.lead_a, self.lead_b = lead_a, lead_b
        self.connection_a, self.contact_a, self.conversation_a = connection_a, contact_a, conversation_a

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_ctwa_retry_first_last_assisted_and_canonical_revenue(self):
        start = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            key = ctwa_idempotency_key(empresa_id=self.tenant_a, provider="meta_whatsapp", external_message_id="wamid.1")
            first_id, inserted = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="meta_webhook",
                idempotency_key=key, occurred_at=start, provider="meta_whatsapp", channel="whatsapp",
                external_event_id="wamid.1", referral={"ctwa_clid": "click-123456789", "source_id": "ad-42", "source_type": "ad", "headline": "Ad headline", "body": "Public ad copy", "access_token": "must-not-persist"},
            )
            duplicate_id, inserted_again = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="retry",
                idempotency_key=key, occurred_at=start, provider="meta_whatsapp", channel="whatsapp",
                external_event_id="wamid.1", referral={"ctwa_clid": "click-123456789", "source_id": "ad-42", "source_type": "ad"},
            )
            self.assertTrue(inserted)
            self.assertFalse(inserted_again)
            self.assertEqual(first_id, duplicate_id)
            campaign_id, recipient_id = uuid.uuid4(), uuid.uuid4()
            # campaign FKs are exercised on a separate tenant-safe row.
            await session.execute(text("INSERT INTO campanhas_disparo(empresa_id,id) VALUES (:tenant,:id)"), {"tenant": self.tenant_a, "id": campaign_id})
            await session.execute(text("INSERT INTO campaign_recipients(empresa_id,id,campanha_id) VALUES (:tenant,:id,:campaign)"), {"tenant": self.tenant_a, "id": recipient_id, "campaign": campaign_id})
            sent_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="campaign_sent", touch_type="campaign", touch_source="campaign_central",
                idempotency_key="campaign-sent-idempotency", occurred_at=start + timedelta(hours=1),
                connection_id=self.connection_a, contact_id=self.contact_a, conversation_id=self.conversation_a,
                outbound_campaign_id=campaign_id, campaign_recipient_id=recipient_id,
            )
            reply_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="campaign_reply", touch_type="campaign", touch_source="campaign_central",
                idempotency_key="campaign-reply-idempotency", occurred_at=start + timedelta(hours=2),
                connection_id=self.connection_a, contact_id=self.contact_a, conversation_id=self.conversation_a,
                outbound_campaign_id=campaign_id, campaign_recipient_id=recipient_id,
            )
            checkout_id, checkout_inserted = await record_checkout_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                checkout_reference="checkout-ref-1", checkout_id="preference-1",
                provider="mercado_pago", occurred_at=start + timedelta(hours=2, minutes=30),
            )
            self.assertTrue(checkout_inserted)
            approved_id, approved_inserted = await record_payment_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                checkout_reference="checkout-ref-1", payment_id="pay-1", provider="mercado_pago",
                status="approved", occurred_at=start + timedelta(hours=3), amount="149.90", currency="BRL",
            )
            later_touch_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="campaign_sent", touch_type="campaign", touch_source="campaign_central",
                idempotency_key="later-campaign-touch", occurred_at=start + timedelta(hours=3, minutes=30),
                connection_id=self.connection_a, contact_id=self.contact_a, conversation_id=self.conversation_a,
                outbound_campaign_id=campaign_id, campaign_recipient_id=recipient_id,
            )
            duplicate_payment_id, duplicate_payment_inserted = await record_payment_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                checkout_reference="checkout-ref-1", payment_id="pay-1", provider="mercado_pago",
                status="approved", occurred_at=start + timedelta(hours=3), amount="149.90", currency="BRL",
            )
            refund_id, refund_inserted = await record_payment_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                checkout_reference="checkout-ref-1", payment_id="pay-1", provider="mercado_pago",
                status="refunded", occurred_at=start + timedelta(hours=4), amount="20.00", currency="BRL",
            )
            self.assertTrue(approved_inserted)
            self.assertFalse(duplicate_payment_inserted)
            self.assertEqual(approved_id, duplicate_payment_id)
            self.assertTrue(refund_inserted)
            await session.commit()
            canonical = list((await session.execute(text("SELECT event_name, revenue_verified, value, currency, external_dispatch_policy, attribution_snapshot FROM commercial_events WHERE empresa_id=:tenant ORDER BY event_name, occurred_at"), {"tenant": self.tenant_a})).all())
            self.assertEqual([row.event_name for row in canonical], ["InitiateCheckout", "Purchase", "Refund"])
            self.assertTrue(next(row for row in canonical if row.event_name == "Purchase").revenue_verified)
            self.assertFalse(canonical_dispatch_decision(SimpleNamespace(is_backfilled=True, external_dispatch_policy="historical_no_send"), mode="live")["dispatch"])
            self.assertFalse(canonical_dispatch_decision(SimpleNamespace(is_backfilled=False, external_dispatch_policy="disabled"), mode="live")["dispatch"])
            result = await get_attribution_for_lead(session, empresa_id=self.tenant_a, lead_id=self.lead_a)
            self.assertEqual(result["first_touch"]["id"], str(first_id))
            self.assertEqual(result["last_touch"]["id"], str(later_touch_id))
            self.assertEqual((await get_first_touch(session, empresa_id=self.tenant_a, lead_id=self.lead_a))["id"], str(first_id))
            self.assertEqual((await get_last_touch(session, empresa_id=self.tenant_a, lead_id=self.lead_a))["id"], str(later_touch_id))
            self.assertEqual({item["id"] for item in result["assisted_touches"]}, {str(sent_id), str(reply_id)})
            self.assertEqual(result["revenue"], [{"currency": "BRL", "amount": 129.9}])
            self.assertEqual([item["id"] for item in result["conversion"]], [str(checkout_id), str(approved_id), str(refund_id)])
            checkout_links = await session.execute(text("SELECT connection_id,contact_id,conversation_id FROM attribution_touches WHERE id=:id"), {"id": checkout_id})
            self.assertEqual(tuple(checkout_links.one()), (self.connection_a, self.contact_a, self.conversation_a))
            payment_links = (await session.execute(text("SELECT event_kind,attribution_touch_id FROM attribution_touches WHERE payment_id='pay-1' ORDER BY event_kind"))).all()
            approved_attribution = next(row.attribution_touch_id for row in payment_links if row.event_kind == "payment_approved")
            refund_attribution = next(row.attribution_touch_id for row in payment_links if row.event_kind == "refund")
            self.assertEqual(approved_attribution, reply_id)
            self.assertEqual(refund_attribution, approved_attribution)
            self.assertEqual(result["first_touch"]["resolution_status"], "partial")
            self.assertEqual(result["first_touch"]["ctwa_clid"], "clic…6789")
            self.assertEqual(result["first_touch"]["headline"], "Ad headline")
            self.assertEqual(result["first_touch"]["body"], "Public ad copy")
            self.assertNotIn("headline", result["first_touch"]["metadata"])
            self.assertNotIn("access_token", str(result))

            summary = await campaign_attribution_summary(session, empresa_id=self.tenant_a)
            campaign_group = next(item for item in summary["items"] if item["outbound_campaign_id"] == str(campaign_id))
            self.assertEqual(campaign_group["checkouts"], {"value": 1, "state": "observed"})
            self.assertEqual(campaign_group["purchases"], {"value": 1, "state": "observed"})
            self.assertEqual(campaign_group["revenue"], [{"currency": "BRL", "value": 129.9, "state": "observed"}])
            self.assertEqual(campaign_group["conversations"], {"value": 1, "state": "observed"})

            other_conversation_id = uuid.uuid4()
            await session.execute(text("INSERT INTO channel_conversations(empresa_id,id,conexao_id,contact_id,lead_id,last_message_at,criado_em) VALUES (:tenant,:id,:connection,:contact,:lead,now(),now())"), {
                "tenant": self.tenant_a, "id": other_conversation_id, "connection": self.connection_a,
                "contact": self.contact_a, "lead": self.lead_a,
            })
            other_conversation = await get_attribution_for_conversation(
                session, empresa_id=self.tenant_a, conversation_id=other_conversation_id,
            )
            self.assertIsNone(other_conversation["first_touch"])
            self.assertEqual(other_conversation["conversation_touches"], [])

            no_conversion_lead = uuid.uuid4()
            await session.execute(text("INSERT INTO crm_leads(empresa_id,id) VALUES (:tenant,:lead)"), {
                "tenant": self.tenant_a, "lead": no_conversion_lead,
            })
            await record_touch(
                session, empresa_id=self.tenant_a, lead_id=no_conversion_lead,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="test",
                idempotency_key="no-conversion-first-touch", occurred_at=start + timedelta(hours=5),
                referral={"ctwa_clid": "no-conversion-click"},
            )
            await record_touch(
                session, empresa_id=self.tenant_a, lead_id=no_conversion_lead,
                event_kind="campaign_sent", touch_type="campaign", touch_source="test",
                idempotency_key="no-conversion-assist-touch", occurred_at=start + timedelta(hours=6),
            )
            no_conversion_projection = await get_attribution_for_lead(
                session, empresa_id=self.tenant_a, lead_id=no_conversion_lead,
            )
            self.assertEqual(no_conversion_projection["assisted_touches"], [])

    async def test_cross_tenant_foreign_key_and_read_scope(self):
        async with self.session_factory() as session:
            with self.assertRaises(IntegrityError):
                await record_touch(
                    session, empresa_id=self.tenant_a, lead_id=self.lead_b,
                    event_kind="ctwa_referral", touch_type="paid_social", touch_source="test",
                    idempotency_key="cross-tenant-forbidden", referral={"ctwa_clid": "click"},
                )
            await session.rollback()
            self.assertIsNone(await get_attribution_for_lead(session, empresa_id=self.tenant_a, lead_id=self.lead_b))

    async def test_commercial_backfill_is_idempotent_and_never_dispatches(self):
        tag_id, tag_event_id, meta_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        occurred = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        instruction = "Managed funnel\n[AGENTEOS_MONITOR_FUNNEL]\n" + json.dumps({
            "meta_event_name": "LeadSubmitted", "funnel_stage_key": "lead_established", "funnel_version": "test-v1",
        })
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO tags_crm(empresa_id,id,instrucao_ia) VALUES (:tenant,:tag,:instruction)"), {
                "tenant": self.tenant_a, "tag": tag_id, "instruction": instruction,
            })
            second_tag_event_id = uuid.uuid4()
            await connection.execute(text("INSERT INTO crm_tag_events(id,empresa_id,lead_id,tag_id,action,source,occurred_at,idempotency_key) VALUES (:id,:tenant,:lead,:tag,'applied','manual',:occurred,'tag-historic-1'),(:second_id,:tenant,:lead,:tag,'applied','ai',:second_occurred,'tag-historic-2')"), {
                "id": tag_event_id, "second_id": second_tag_event_id,
                "tenant": self.tenant_a, "lead": self.lead_a, "tag": tag_id,
                "occurred": occurred, "second_occurred": occurred + timedelta(minutes=1),
            })
            await connection.execute(text("INSERT INTO meta_conversion_events(id,empresa_id,lead_id,tag_id,event_name,event_id,status,source,trigger_type,trigger_ref,trigger_source_event_id,criado_em) VALUES (:id,:tenant,:lead,:tag,'LeadSubmitted','legacy-event-preserved','dry_run','tag','tag_applied',:tag_ref,:event_ref,:occurred)"), {
                "id": meta_id, "tenant": self.tenant_a, "lead": self.lead_a, "tag": tag_id,
                "tag_ref": str(tag_id), "event_ref": str(tag_event_id), "occurred": occurred,
            })
        async with self.session_factory() as session:
            dry = await commercial_event_backfill(session, empresa_id=self.tenant_a, dry_run=True)
            self.assertEqual(dry["would_insert"], 1)
            self.assertEqual(dry["skipped_duplicate"], 1)
            self.assertEqual(dry["inserted"], 0)
            result = await commercial_event_backfill(session, empresa_id=self.tenant_a, dry_run=False)
            self.assertEqual(result["inserted"], 1)
            self.assertEqual(result["reconciliation"]["classifications"].get("LINKED_EXACT"), 1)
            row = await session.scalar(select(CanonicalCommercialEvent).where(
                CanonicalCommercialEvent.empresa_id == self.tenant_a,
                CanonicalCommercialEvent.tag_event_id == tag_event_id,
            ))
            self.assertIsNotNone(row)
            self.assertTrue(row.is_backfilled)
            self.assertEqual(row.external_dispatch_policy, "historical_no_send")
            self.assertFalse(canonical_dispatch_decision(row, mode="live")["dispatch"])
            second = await commercial_event_backfill(session, empresa_id=self.tenant_a, dry_run=False)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["skipped_duplicate"], 2)
            legacy = (await session.execute(text("SELECT event_id,status FROM meta_conversion_events WHERE id=:id"), {"id": meta_id})).one()
            self.assertEqual(tuple(legacy), ("legacy-event-preserved", "dry_run"))
            link = (await session.execute(text("SELECT classification,commercial_event_id FROM commercial_event_meta_links WHERE empresa_id=:tenant AND meta_event_id=:id"), {"tenant": self.tenant_a, "id": meta_id})).one()
            self.assertEqual(link.classification, "LINKED_EXACT")
            self.assertEqual(link.commercial_event_id, row.id)

    async def test_manual_and_monitor_managed_tag_share_one_commercial_event(self):
        tag_id, manual_event_id, monitor_event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        occurred = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        instruction = "Managed funnel\n[AGENTEOS_MONITOR_FUNNEL]\n" + json.dumps({
            "meta_event_name": "LeadSubmitted", "funnel_stage_key": "lead_established", "funnel_version": "test-v1",
        })
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO tags_crm(empresa_id,id,instrucao_ia) VALUES (:tenant,:tag,:instruction)"), {
                "tenant": self.tenant_a, "tag": tag_id, "instruction": instruction,
            })
            await connection.execute(text("INSERT INTO crm_tag_events(id,empresa_id,lead_id,tag_id,action,source,occurred_at,idempotency_key,metadata_json) VALUES (:manual,:tenant,:lead,:tag,'applied','manual',:occurred,'manual-application',:manual_metadata),(:monitor,:tenant,:lead,:tag,'applied','ai',:later,'monitor-application',:monitor_metadata)"), {
                "manual": manual_event_id, "monitor": monitor_event_id,
                "tenant": self.tenant_a, "lead": self.lead_a, "tag": tag_id,
                "occurred": occurred, "later": occurred + timedelta(minutes=1),
                "manual_metadata": json.dumps({"tag_application_source": "manual"}),
                "monitor_metadata": json.dumps({"tag_application_source": "monitor"}),
            })
        async with self.session_factory() as session:
            events = list((await session.execute(select(CRMTagEvent).where(
                CRMTagEvent.empresa_id == self.tenant_a,
                CRMTagEvent.id.in_([manual_event_id, monitor_event_id]),
            ).order_by(CRMTagEvent.occurred_at.asc()))).scalars().all())
            first_id, inserted = await record_managed_tag_commercial_event(
                session, tag_event=events[0], event_name="LeadSubmitted",
                funnel_stage_id="lead_established", metadata_json={"funnel_version": "test-v1"},
            )
            second_id, inserted_again = await record_managed_tag_commercial_event(
                session, tag_event=events[1], event_name="LeadSubmitted",
                funnel_stage_id="lead_established", metadata_json={"funnel_version": "test-v1"},
            )
            await session.commit()
            self.assertTrue(inserted)
            self.assertFalse(inserted_again)
            self.assertEqual(first_id, second_id)
            self.assertEqual(await session.scalar(text(
                "SELECT count(*) FROM commercial_events WHERE empresa_id=:tenant AND event_name='LeadSubmitted'"
            ), {"tenant": self.tenant_a}), 1)

    async def test_ads_insights_upsert_is_tenant_scoped_and_idempotent(self):
        from app.services.meta import ads_insights

        config_id = uuid.uuid4()
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO traffic_meta_configs(id,empresa_id,business_id,ad_account_id,status) VALUES (:id,:tenant,'business-1','123456','active')"), {
                "id": config_id, "tenant": self.tenant_a,
            })
        response = {
            "id": "account", "account_id": "123456", "name": "Tenant Account", "currency": "BRL",
            "business": {"id": "business-1"},
        }
        insight_row = {
            "date_start": "2026-10-01", "date_stop": "2026-10-01", "campaign_id": "campaign-1",
            "campaign_name": "Campaign", "adset_id": "adset-1", "adset_name": "Adset",
            "ad_id": "ad-1", "ad_name": "Ad", "spend": "12.50", "impressions": "1000",
            "reach": "800", "frequency": "1.25", "clicks": "50", "ctr": "5.0", "cpc": "0.25",
            "cpm": "12.5", "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "4"}],
            "action_values": [],
        }

        class FakeClient:
            def __init__(self, row):
                self.row = row

            async def validate_ad_account(self, _account_id):
                return response

            async def get_insights(self, *_args, **_kwargs):
                return [self.row]

        credential = SimpleNamespace(access_token="tenant-token-not-logged", graph_version="v23.0", read_only=True)
        with patch.object(ads_insights.MetaMarketingCredentialResolver, "resolve", new=AsyncMock(return_value=credential)), \
             patch.object(ads_insights, "MetaMarketingApiClient", side_effect=lambda *args, **kwargs: FakeClient(insight_row)):
            async with self.session_factory() as session:
                result = await ads_insights.sync_ads_insights(
                    session, empresa_id=self.tenant_a, start_date=date(2026, 10, 1), end_date=date(2026, 10, 1), dry_run=False,
                )
                await session.commit()
                self.assertEqual((result["rows_fetched"], result["rows_upserted"]), (3, 3))
            insight_row["spend"] = "15.00"
            with patch.object(ads_insights, "MetaMarketingApiClient", side_effect=lambda *args, **kwargs: FakeClient(insight_row)):
                async with self.session_factory() as session:
                    await ads_insights.sync_ads_insights(
                        session, empresa_id=self.tenant_a, start_date=date(2026, 10, 1), end_date=date(2026, 10, 1), dry_run=False,
                    )
                    await session.commit()
                    count = await session.scalar(text("SELECT count(*) FROM meta_ads_insights_daily WHERE empresa_id=:tenant"), {"tenant": self.tenant_a})
                    spend = await session.scalar(text("SELECT spend FROM meta_ads_insights_daily WHERE empresa_id=:tenant"), {"tenant": self.tenant_a})
                    self.assertEqual(count, 3)
                    self.assertEqual(str(spend), "15.000000")
                    levels = await session.scalars(text("SELECT DISTINCT entity_level FROM meta_ads_insights_daily WHERE empresa_id=:tenant"), {"tenant": self.tenant_a})
                    self.assertEqual(set(levels.all()), {"campaign", "adset", "ad"})

    async def test_ad_resolution_records_missing_tenant_permission_without_global_fallback(self):
        from app.services.meta.conversion_credentials import MetaCredentialResolutionError
        from app.services.meta.marketing_credentials import MetaMarketingCredentialResolver

        touch_id = None
        async with self.session_factory() as session:
            await session.execute(text("INSERT INTO traffic_meta_configs(id,empresa_id,business_id,ad_account_id,status) VALUES (:id,:tenant,'business-1','123456','active')"), {
                "id": uuid.uuid4(), "tenant": self.tenant_a,
            })
            touch_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="meta_webhook",
                idempotency_key="ad-resolution-permission", referral={
                    "ctwa_clid": "resolution-click", "source_id": "ad-42", "source_type": "ad",
                },
            )
            await session.commit()
        with patch.object(MetaMarketingCredentialResolver, "resolve", new=AsyncMock(side_effect=MetaCredentialResolutionError("TENANT_MARKETING_PERMISSION_REQUIRED"))) as resolver:
            async with self.session_factory() as session:
                state = await resolve_meta_ad_source(session, empresa_id=self.tenant_a, touch_id=touch_id)
                self.assertEqual(state, "forbidden")
                resolver.assert_awaited_once()
                self.assertTrue(resolver.await_args.kwargs["allow_admin_fallback"])
                status, attempted, code = (await session.execute(text("SELECT resolution_status,last_attempt_at,error_code FROM attribution_touch_resolutions WHERE empresa_id=:tenant AND touch_id=:touch"), {
                    "tenant": self.tenant_a, "touch": touch_id,
                })).one()
                self.assertEqual(status, "forbidden")
                self.assertIsNotNone(attempted)
                self.assertEqual(code, "TENANT_MARKETING_PERMISSION_REQUIRED")

    async def test_analytics_first_touch_last_touch_and_tenant_scope(self):
        from app.services.attribution_ledger import record_touch

        occurred = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            await session.execute(text(
                "UPDATE channel_conversations SET criado_em=:occurred WHERE empresa_id=:tenant AND id=:conversation"
            ), {"occurred": occurred, "tenant": self.tenant_a, "conversation": self.conversation_a})
            first_touch_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="meta_webhook",
                idempotency_key="analytics-first-touch", occurred_at=occurred,
                referral={"ctwa_clid": "analytics-click", "source_id": "ad-one", "source_type": "ad"},
            )
            last_touch_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="campaign_sent", touch_type="campaign", touch_source="campaign_central",
                idempotency_key="analytics-last-touch", occurred_at=occurred + timedelta(hours=1),
            )
            first_resolution = await session.scalar(select(AttributionTouchResolution).where(AttributionTouchResolution.touch_id == first_touch_id))
            last_resolution = await session.scalar(select(AttributionTouchResolution).where(AttributionTouchResolution.touch_id == last_touch_id))
            first_resolution.adset_id, first_resolution.campaign_id = "adset-one", "campaign-one"
            first_resolution.resolution_status = "resolved"
            last_resolution.adset_id, last_resolution.campaign_id = "adset-two", "campaign-two"
            last_resolution.resolution_status = "resolved"
            await record_commercial_event(
                session, empresa_id=self.tenant_a, event_name="LeadSubmitted", source_type="monitor",
                source_id="monitor-transition-one", lead_id=self.lead_a,
                occurred_at=occurred + timedelta(hours=2),
            )
            await record_commercial_event(
                session, empresa_id=self.tenant_b, event_name="LeadSubmitted", source_type="monitor",
                source_id="tenant-b-event", lead_id=self.lead_b,
                occurred_at=occurred + timedelta(hours=2),
            )
            await session.commit()
            first = await performance(session, empresa_id=self.tenant_a, start_date=date(2026, 10, 1), end_date=date(2026, 10, 1), attribution_model="first_touch", level="campaign")
            last = await performance(session, empresa_id=self.tenant_a, start_date=date(2026, 10, 1), end_date=date(2026, 10, 1), attribution_model="last_touch", level="campaign")
            self.assertEqual({row["entity_id"] for row in first["rows"]}, {"campaign-one"})
            self.assertEqual({row["entity_id"] for row in last["rows"]}, {"campaign-two"})
            self.assertEqual(first["rows"][0]["leads"]["value"], 1)
            self.assertEqual(last["rows"][0]["spend"]["state"], "unknown")
            self.assertEqual((await session.scalar(text("SELECT count(*) FROM commercial_events WHERE empresa_id=:tenant"), {"tenant": self.tenant_a})), 1)

    async def test_unresolved_source_and_safe_url(self):
        async with self.session_factory() as session:
            touch_id, _ = await record_touch(
                session, empresa_id=self.tenant_a, lead_id=self.lead_a,
                event_kind="ctwa_referral", touch_type="paid_social", touch_source="test",
                idempotency_key="unresolved-source", referral={
                    "ctwa_clid": "click-1", "source_id": "post-42", "source_type": "post",
                    "source_url": "https://example.test/landing?token=should-not-persist&utm_source=meta#frag",
                },
            )
            await session.commit()
            result = await get_attribution_for_lead(session, empresa_id=self.tenant_a, lead_id=self.lead_a)
            self.assertEqual(result["last_touch"]["resolution_status"], "unresolved")
            self.assertEqual(result["last_touch"]["source_url"], "https://example.test/landing?utm_source=meta")
            self.assertNotIn("should-not-persist", str(result))
            self.assertNotEqual(result["last_touch"]["id"], str(uuid.uuid4()))
            self.assertEqual(clean_referral(None), {})

    async def test_meta_inbound_referral_fact_is_post_commit_idempotent_and_optional(self):
        message_id = uuid.uuid4()
        occurred = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO mensagens_historico(id,empresa_id,lead_id,conexao_id,channel_conversation_id,channel,provider,external_message_id,direction,criado_em,metadata_json) VALUES (:id,:tenant,:lead,:connection,:conversation,'whatsapp','meta_whatsapp','wamid-ledger-1','inbound',:created,'{}')"), {
                "id": message_id, "tenant": self.tenant_a, "lead": self.lead_a,
                "connection": self.connection_a, "conversation": self.conversation_a, "created": occurred,
            })
        event = meta_webhook_router.CanonicalMetaWebhookEvent(
            channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
            external_account_id="phone-number-id", external_event_id="wamid-ledger-1",
            external_message_id="wamid-ledger-1", event_kind="message", timestamp=str(int(occurred.timestamp())),
            referral={"ctwa_clid": "ctwa-canary-fact", "source_id": "post-42", "source_type": "post"},
        )
        operational = meta_webhook_router.ResolvedMetaConnection(
            empresa_id=str(self.tenant_a), connection_id=str(self.connection_a),
            channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
        )
        message = SimpleNamespace(
            id=message_id, empresa_id=self.tenant_a, lead_id=self.lead_a,
            channel_conversation_id=self.conversation_a, criado_em=occurred,
        )
        with patch.object(meta_webhook_router, "AsyncSessionLocal", self.session_factory):
            await meta_webhook_router._capture_ctwa_attribution_touch(event, operational, message)
            await meta_webhook_router._capture_ctwa_attribution_touch(event, operational, message)
            no_referral = event.__class__(
                channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP,
                external_account_id="phone-number-id", external_event_id="wamid-organic",
                external_message_id="wamid-organic", event_kind="message", referral=None,
            )
            await meta_webhook_router._capture_ctwa_attribution_touch(no_referral, operational, message)
        async with self.session_factory() as session:
            count = await session.scalar(text("SELECT count(*) FROM attribution_touches WHERE empresa_id=:tenant AND event_kind='ctwa_referral'"), {"tenant": self.tenant_a})
            row = (await session.execute(text("SELECT message_id,contact_id,conversation_id,lead_id,resolution_status FROM attribution_touches t JOIN attribution_touch_resolutions r ON r.empresa_id=t.empresa_id AND r.touch_id=t.id WHERE t.empresa_id=:tenant AND t.event_kind='ctwa_referral'"), {"tenant": self.tenant_a})).one()
            self.assertEqual(count, 1)
            self.assertEqual(tuple(row), (message_id, self.contact_a, self.conversation_a, self.lead_a, "unresolved"))

    async def test_backfill_dry_run_apply_and_retry_are_idempotent(self):
        legacy = {"meta_attribution": {
            "first_touch": {"ctwa_clid": "legacy-click-1", "source_id": "legacy-ad", "source_type": "ad", "captured_at": "2026-09-01T12:00:00Z"},
            "last_touch": {"ctwa_clid": "legacy-click-1", "source_id": "legacy-ad", "source_type": "ad", "captured_at": "2026-09-01T12:00:00Z"},
        }}
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE crm_leads SET dados_adicionais=:extra WHERE empresa_id=:tenant AND id=:lead"), {"extra": json.dumps(legacy), "tenant": self.tenant_a, "lead": self.lead_a})
            campaign_id, recipient_id = uuid.uuid4(), uuid.uuid4()
            await connection.execute(text("INSERT INTO campanhas_disparo(empresa_id,id) VALUES (:tenant,:id)"), {"tenant": self.tenant_a, "id": campaign_id})
            await connection.execute(text("INSERT INTO campaign_recipients(empresa_id,id,provider,channel,sent_at,external_message_id,contact_id,conversation_id,lead_id,conexao_id,campanha_id,status) VALUES (:tenant,:id,'meta_whatsapp','whatsapp',:sent,'wamid-backfill',:contact,:conversation,:lead,:connection,:campaign,'delivered')"), {
                "tenant": self.tenant_a, "id": recipient_id, "sent": datetime(2026, 9, 2, 12),
                "contact": self.contact_a, "conversation": self.conversation_a, "lead": self.lead_a,
                "connection": self.connection_a, "campaign": campaign_id,
            })
        async with self.session_factory() as session:
            preview = await backfill_attribution(session, dry_run=True)
            self.assertEqual(preview["rows"], 2)
            self.assertEqual(preview["would_insert"], 2)
            result = await backfill_attribution(session, dry_run=False)
            self.assertEqual(result["inserted"], 2)
            retry = await backfill_attribution(session, dry_run=True)
            self.assertEqual(retry["would_insert"], 0)
            self.assertEqual(retry["skipped_duplicate"], 2)
            self.assertEqual(await reconcile_legacy_attribution(session), {"match": 1, "partial": 0, "conflict": 0, "missing": 0})
            stored = await session.scalar(text("SELECT dados_adicionais FROM crm_leads WHERE empresa_id=:tenant AND id=:lead"), {"tenant": self.tenant_a, "lead": self.lead_a})
            self.assertEqual(stored, legacy)


if __name__ == "__main__":
    unittest.main()
