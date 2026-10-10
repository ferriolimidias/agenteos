from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pathlib
import unittest
from unittest.mock import AsyncMock

from app.services.conversation_window import evaluate_conversation_window
from app.services.message_status_service import should_advance_status
from app.services.outbox_service import deterministic_idempotency_key
from app.services.meta_webhook import (
    CanonicalMetaWebhookEvent,
    MetaWebhookProcessResult,
    ResolvedMetaConnection,
    process_meta_webhook,
)
from app.services.channels.types import Channel, Provider
from db.models import (
    CampaignRecipient,
    CommunicationConsent,
    FollowupExecution,
    MessageAttempt,
    MessageOutbox,
    MessageStatusEvent,
)


ROOT = pathlib.Path(__file__).resolve().parents[2]


class PhaseAFoundationModelTests(unittest.TestCase):
    def test_operational_tables_have_tenant_safe_idempotency_constraints(self):
        expectations = {
            CampaignRecipient: {"uq_campaign_recipients_idempotency", "fk_campaign_recipients_empresa_conexao"},
            MessageOutbox: {"uq_message_outbox_idempotency", "fk_message_outbox_empresa_conexao"},
            MessageAttempt: {"uq_message_attempts_attempt", "fk_message_attempts_empresa_outbox"},
            FollowupExecution: {"uq_followup_executions_idempotency", "fk_followup_executions_empresa_conexao"},
            CommunicationConsent: {"uq_communication_consents_scope", "fk_communication_consents_empresa_contact"},
            MessageStatusEvent: {"uq_message_status_events_idempotency", "fk_message_status_events_empresa_conexao"},
        }
        for model, names in expectations.items():
            constraints = {constraint.name for constraint in model.__table__.constraints}
            self.assertTrue(names.issubset(constraints), f"{model.__name__}: {names - constraints}")

    def test_required_operational_columns_exist(self):
        for model, columns in (
            (CampaignRecipient, ("empresa_id", "conexao_id", "provider", "channel", "contact_id", "idempotency_key", "external_message_id")),
            (MessageOutbox, ("empresa_id", "conexao_id", "operation_type", "payload_json", "locked_at", "locked_by", "attempts", "max_attempts")),
            (FollowupExecution, ("empresa_id", "followup_id", "lead_id", "contact_id", "conversation_id", "conexao_id", "idempotency_key")),
            (CommunicationConsent, ("empresa_id", "contact_id", "channel", "provider", "status", "source", "revoked_at", "blocked_at")),
            (MessageStatusEvent, ("empresa_id", "conexao_id", "external_message_id", "external_event_id", "status", "idempotency_key")),
        ):
            table_columns = model.__table__.columns
            for column in columns:
                self.assertIn(column, table_columns)

    def test_migration_and_rollback_are_separate_and_cover_phase_a_tables(self):
        upgrade = (ROOT / "db/migrations/2026_07_20_phase_a_operational_foundation.sql").read_text(encoding="utf-8")
        rollback = (ROOT / "db/migrations/2026_07_20_phase_a_operational_foundation_rollback.sql").read_text(encoding="utf-8")
        for table in (
            "campaign_recipients",
            "message_outbox",
            "message_attempts",
            "followup_executions",
            "communication_consents",
            "message_status_events",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", upgrade)
            self.assertIn(f"DROP TABLE IF EXISTS {table}", rollback)
        self.assertIn("precheck failed", upgrade)
        self.assertIn("postcheck failed", upgrade)


class PhaseAFoundationBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def test_idempotency_key_is_deterministic_and_tenant_scoped(self):
        a1 = deterministic_idempotency_key("campaign", "tenant-a", "campaign-1", "recipient-1")
        a2 = deterministic_idempotency_key("campaign", "tenant-a", "campaign-1", "recipient-1")
        b = deterministic_idempotency_key("campaign", "tenant-b", "campaign-1", "recipient-1")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)

    def test_window_states(self):
        class Conversation:
            pass

        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        conv = Conversation()
        conv.last_inbound_at = now - timedelta(hours=23, minutes=59)
        self.assertEqual(evaluate_conversation_window(conversation=conv, provider="meta_whatsapp", channel="whatsapp", now=now).state, "open")
        conv.last_inbound_at = now - timedelta(hours=24, minutes=1)
        self.assertEqual(evaluate_conversation_window(conversation=conv, provider="meta_whatsapp", channel="whatsapp", now=now).state, "closed")
        conv.last_inbound_at = None
        self.assertEqual(evaluate_conversation_window(conversation=conv, provider="meta_whatsapp", channel="whatsapp", now=now).state, "unknown")
        self.assertEqual(evaluate_conversation_window(conversation=conv, provider="meta_instagram", channel="instagram", now=now).state, "unsupported")

    def test_status_precedence_blocks_regressions(self):
        self.assertTrue(should_advance_status("sent", "delivered"))
        self.assertTrue(should_advance_status("delivered", "read"))
        self.assertFalse(should_advance_status("read", "delivered"))
        self.assertFalse(should_advance_status("delivered", "sent"))
        self.assertFalse(should_advance_status("read", "failed"))

    async def test_webhook_handler_is_called_only_for_accepted_non_duplicate_event(self):
        event = CanonicalMetaWebhookEvent(
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
            external_account_id="phone-1",
            external_event_id="wamid.test",
            event_kind="status",
            status="delivered",
        )
        resolver = AsyncMock(
            return_value=ResolvedMetaConnection(
                empresa_id="tenant-a",
                connection_id="conn-a",
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
            )
        )
        dedup = AsyncMock(return_value=True)
        handler = AsyncMock(return_value=False)
        with unittest.mock.patch("app.services.meta_webhook.verify_meta_signature", return_value=True), unittest.mock.patch(
            "app.services.meta_webhook.parse_meta_payload", return_value={}
        ), unittest.mock.patch("app.services.meta_webhook.normalize_meta_payload", return_value=[event]):
            result = await process_meta_webhook(
                b"{}",
                "sha256=" + "0" * 64,
                "secret",
                resolve_connection=resolver,
                acquire_dedup=dedup,
                handle_event=handler,
            )
        self.assertEqual(result, MetaWebhookProcessResult(received=1, accepted=1, duplicates=0, unresolved=0))
        handler.assert_awaited_once()

        # A busy/missing Redis lease is not proof that PostgreSQL accepted the
        # event. The durable handler still decides whether it is a duplicate.
        dedup = AsyncMock(return_value=False)
        handler = AsyncMock(return_value=False)
        with unittest.mock.patch("app.services.meta_webhook.verify_meta_signature", return_value=True), unittest.mock.patch(
            "app.services.meta_webhook.parse_meta_payload", return_value={}
        ), unittest.mock.patch("app.services.meta_webhook.normalize_meta_payload", return_value=[event]):
            result = await process_meta_webhook(
                b"{}",
                "sha256=" + "0" * 64,
                "secret",
                resolve_connection=resolver,
                acquire_dedup=dedup,
                handle_event=handler,
            )
        self.assertEqual(result, MetaWebhookProcessResult(received=1, accepted=1, duplicates=0, unresolved=0))
        handler.assert_awaited_once()


class PhaseAStaticSafetyTests(unittest.TestCase):
    def test_campaign_worker_filters_connection_by_tenant_and_no_longer_calls_evolution_send(self):
        source = (ROOT / "app/services/campanha_service.py").read_text(encoding="utf-8")
        self.assertIn("Conexao.empresa_id == campanha.empresa_id", source)
        self.assertIn("dispatch_outbox_now", source)
        self.assertIn("despachar_mensagem", source)
        self.assertIn("upsert_campaign_recipient", source)
        self.assertIn("enqueue_message", source)

    def test_followup_worker_reuses_outbox_and_dispatches_without_consumer(self):
        service = (ROOT / "app/services/followup_service.py").read_text(encoding="utf-8")
        engine = (ROOT / "app/services/followup_engine.py").read_text(encoding="utf-8")
        self.assertIn("followup_engine", service)
        self.assertIn("processar_followups_pendentes", service)
        self.assertNotIn("dispatch_outbox_now", service)
        self.assertIn("dispatch_outbox_now", engine)
        self.assertIn("despachar_mensagem", engine)
        self.assertIn("upsert_followup_execution", engine)
        self.assertIn("operation_type=\"followup_prepare_send\"", engine)
        self.assertIn("cancel_on_inbound", engine)

    def test_inbox_multichannel_compatibility_routes_exist_without_replacing_legacy_routes(self):
        source = (ROOT / "app/api/routers/inbox.py").read_text(encoding="utf-8")
        self.assertIn('"/{empresa_id}/inbox/conversas"', source)
        self.assertIn('"/{empresa_id}/inbox/{telefone}"', source)
        self.assertIn("ChannelConversation.empresa_id == empresa_uuid", source)


if __name__ == "__main__":
    unittest.main()
