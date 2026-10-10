import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from app.services.campaign_central_dispatch_service import (
    DEFAULT_AGENTOS_MPS,
    MAX_SEND_ATTEMPTS,
    META_DEFAULT_MPS,
    META_MAX_MPS,
    _classify_meta_send_error,
    _configured_connection_mps,
    _provider_for_connection,
    acquire_campaign_send_rate_slot,
    campaign_allowlist_enforced,
    campaign_send_mode,
)
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppError
from app.api.routers.conexoes import _campaign_send_mode


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class CampaignCentralRealSendTest(TestCase):
    def test_campaign_start_materializes_recipients_and_outbox_without_legacy_dispatcher(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        router = read("app/api/routers/campaign_central.py")
        self.assertIn("start_campaign_from_snapshot", service)
        self.assertIn("snapshot_id", service)
        self.assertIn("confirmed_recipient_count", service)
        self.assertIn('operation_type="campaign_template_send"', service)
        self.assertIn("CampaignRecipient(", service)
        self.assertIn("enqueue_message(", service)
        self.assertIn("/campaigns/{campaign_id}/start", router)
        self.assertNotIn("processar_campanha_disparo", service)
        self.assertNotIn("processar_campanha_disparo", router)

    def test_campaign_start_requires_server_side_snapshot_confirmation(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        router = read("app/api/routers/campaign_central.py")
        self.assertIn("_parse_start_confirmation", service)
        self.assertIn("Confirmação do snapshot preparado é obrigatória", service)
        self.assertIn("where empresa_id = :empresa_id and campaign_id = :campaign_id and id = :snapshot_id", service)
        self.assertIn("confirmed_count != len(inclusions)", service)
        self.assertIn("Snapshot preparado foi substituído", service)
        self.assertIn("snapshot_id=(payload or {}).get(\"snapshot_id\")", router)
        self.assertIn("confirmed_recipient_count=(payload or {}).get(\"confirmed_recipient_count\")", router)

    def test_campaign_central_is_meta_official_only_and_preserves_evolution_legacy(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("Campanha Central real exige conexão WhatsApp Meta Official", service)
        self.assertIn("_is_meta_official", service)
        self.assertNotIn("Evolution", service)

    def test_campaign_outbox_worker_claims_with_skip_locked_and_calls_canonical_provider(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        main = read("app/api/main.py")
        self.assertIn("claim_campaign_outbox_batch", service)
        self.assertIn(".with_for_update(skip_locked=True)", service)
        self.assertIn("process_campaign_outbox_once", service)
        self.assertIn(".send_template(", service)
        self.assertIn("ChannelOutboundMessage(", service)
        self.assertIn("process_campaign_outbox_once", main)
        self.assertIn("worker_runtime.start_worker(", main)
        self.assertIn('name="campaign"', main)

    def test_campaign_worker_job_logs_are_sanitized_and_classify_outcomes(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("WORKER_JOB_CLAIM worker=campaign claimed=%s claim_mode=for_update_skip_locked", service)
        self.assertIn("WORKER_JOB_SUCCESS worker=campaign outcome=sent", service)
        self.assertIn("WORKER_JOB_RETRY worker=campaign outcome=retry", service)
        self.assertIn('WORKER_JOB_FAILED worker=campaign outcome=%s', service)
        self.assertIn('worker_logger = logging.getLogger("uvicorn.error")', service)

    def test_campaign_lifecycle_pause_resume_cancel_and_results_endpoints_exist(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        router = read("app/api/routers/campaign_central.py")
        for name in ("pause_campaign", "resume_campaign", "cancel_campaign", "campaign_results"):
            self.assertIn(name, service)
        for route in (
            "/campaigns/{campaign_id}/pause",
            "/campaigns/{campaign_id}/resume",
            "/campaigns/{campaign_id}/cancel",
            "/campaigns/{campaign_id}/results",
        ):
            self.assertIn(route, router)
        self.assertIn("cancelled_recipients", service)
        self.assertIn("percentages", service)

    def test_campaign_rechecks_optout_and_allows_delivery_correlation(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        outbox = read("app/services/outbox_service.py")
        status_service = read("app/services/message_status_service.py")
        self.assertIn("communication_allowed(", service)
        self.assertIn("campaign_allowlist_block", service)
        self.assertIn('{"opted_out", "opt_out", "blocked", "revoked"}', outbox)
        self.assertIn("(CampaignRecipient, CampaignRecipient.external_message_id)", status_service)
        self.assertIn("(MessageOutbox, MessageOutbox.external_message_id)", status_service)

    def test_campaign_acceptance_materializes_one_inbox_message_with_campaign_origin(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("_materialize_campaign_history", service)
        self.assertIn('origem_mensagem="campaign"', service)
        self.assertIn('tipo_mensagem="template"', service)
        self.assertIn('MensagemHistorico.external_message_id == external_message_id', service)

    def test_campaign_sent_attribution_is_best_effort_after_provider_acceptance(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("async with session.begin_nested():", service)
        self.assertIn("ATTRIBUTION_CAMPAIGN_SENT_CAPTURE_FAILED", service)
        self.assertIn("recovery=campaign_recipient_backfill", service)

    def test_campaign_history_is_not_an_ai_first_turn(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        utils = read("app/api/utils.py")
        self.assertIn('origem_mensagem="campaign"', service)
        self.assertIn('normalized_origin == "ia"', utils)

    def test_campaign_model_migration_adds_canonical_status_sender_and_template_columns(self):
        migration = read("db/migrations/2026_08_21_campaign_central_real_send.sql")
        models = read("db/models.py")
        for token in ("central_status", "conexao_id", "meta_template_id", "payload_json"):
            self.assertIn(token, migration)
            self.assertIn(token, models)
        self.assertIn("ix_message_outbox_campaign_claim", migration)

    def test_campaign_real_send_allowlist_is_deny_all_by_default(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn('os.getenv("CAMPAIGN_REAL_SEND_ALLOWLIST", "")', service)
        self.assertIn("if not clean:", service)
        self.assertIn("return False", service)

    def test_campaign_real_send_allowlist_matches_e164_digits_and_hash(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn("clean in allowlist", service)
        self.assertIn("digits in allowlist", service)
        self.assertIn("hashlib.sha256", service)
        self.assertIn("digest in allowlist", service)

    def test_production_mode_disables_only_connection_allowlist_gate(self):
        connection = SimpleNamespace(credenciais={"campaign_send_mode": "production"})
        self.assertEqual(campaign_send_mode(connection), "production")
        self.assertFalse(campaign_allowlist_enforced(connection))

    def test_test_only_mode_preserves_allowlist_gate_and_fails_closed(self):
        self.assertEqual(campaign_send_mode(SimpleNamespace(credenciais={})), "test_only")
        self.assertTrue(campaign_allowlist_enforced(SimpleNamespace(credenciais={})))
        self.assertTrue(campaign_allowlist_enforced(SimpleNamespace(credenciais={"campaign_send_mode": "unknown"})))

    def test_campaign_send_mode_api_is_sanitized_and_fails_closed(self):
        self.assertEqual(_campaign_send_mode(SimpleNamespace(credenciais={"campaign_send_mode": "production", "access_token": "secret"})), "production")
        self.assertEqual(_campaign_send_mode(SimpleNamespace(credenciais={"campaign_send_mode": "unknown"})), "test_only")

    def test_campaign_worker_batch_size_is_conservative_and_bounded(self):
        service = read("app/services/campaign_central_dispatch_service.py")
        self.assertIn('os.getenv("CAMPAIGN_WORKER_BATCH_SIZE", "25")', service)
        self.assertIn("min(100", service)
        self.assertIn("return 25", service)

    def test_campaign_send_provider_disables_transport_retries_for_campaigns(self):
        connection = SimpleNamespace(
            empresa_id="00000000-0000-0000-0000-000000000001",
            id="00000000-0000-0000-0000-000000000002",
            phone_number_id="phone-1",
            waba_id="waba-1",
            nome_instancia="meta",
            status="ativo",
            credenciais={"access_token": "secret"},
        )
        provider = _provider_for_connection(connection)
        self.assertEqual(provider.config.max_attempts, 1)

    def test_rate_limiter_100_recipients_respects_10_mps(self):
        fake = FakeRedis()
        now = datetime(2026, 8, 22, 10, 0, 0)
        allowed = asyncio.run(_consume_slots(fake, "phone-a", 100, 10, now))
        self.assertEqual(allowed, 10)

    def test_multi_worker_rate_limit_shares_bucket(self):
        fake = FakeRedis()
        now = datetime(2026, 8, 22, 10, 0, 0)

        async def run():
            results = await asyncio.gather(
                _consume_slots(fake, "phone-a", 10, 10, now),
                _consume_slots(fake, "phone-a", 10, 10, now),
                _consume_slots(fake, "phone-a", 10, 10, now),
                _consume_slots(fake, "phone-a", 10, 10, now),
            )
            return sum(results)

        self.assertEqual(asyncio.run(run()), 10)

    def test_multitenant_rate_limit_isolated_by_phone_number_id(self):
        fake = FakeRedis()
        now = datetime(2026, 8, 22, 10, 0, 0)

        async def run():
            return await asyncio.gather(
                _consume_slots(fake, "phone-a", 20, 10, now),
                _consume_slots(fake, "phone-b", 20, 10, now),
            )

        self.assertEqual(asyncio.run(run()), [10, 10])

    def test_connection_mps_policy_is_configurable_and_capped(self):
        self.assertEqual(DEFAULT_AGENTOS_MPS, 10)
        self.assertEqual(META_DEFAULT_MPS, 80)
        self.assertEqual(META_MAX_MPS, 1000)
        self.assertEqual(MAX_SEND_ATTEMPTS, 3)
        self.assertEqual(_configured_connection_mps(SimpleNamespace(credenciais={})), 10)
        self.assertEqual(_configured_connection_mps(SimpleNamespace(credenciais={"whatsapp_max_mps": "40"})), 40)
        self.assertEqual(_configured_connection_mps(SimpleNamespace(credenciais={"whatsapp_max_mps": "999"})), 80)

    def test_meta_send_error_classification(self):
        self.assertEqual(_classify_meta_send_error(MetaWhatsAppError("rate", http_status=429)), ("rate_limited", "RATE_LIMIT"))
        self.assertEqual(_classify_meta_send_error(MetaWhatsAppError("server", http_status=503)), ("provider_5xx", "TRANSIENT"))
        self.assertEqual(_classify_meta_send_error(MetaWhatsAppError("quality", http_status=400, meta_error={"code": 131048})), ("rate_limited", "RATE_LIMIT"))
        self.assertEqual(_classify_meta_send_error(MetaWhatsAppError("bad", http_status=400, meta_error={"code": 100})), ("meta_whatsapp_error", "PERMANENT"))


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True


async def _consume_slots(fake, phone, count, max_mps, now):
    allowed = 0
    for _ in range(count):
        allowed += int(await acquire_campaign_send_rate_slot(phone_number_id=phone, max_mps=max_mps, now=now, redis_client=fake))
    return allowed
