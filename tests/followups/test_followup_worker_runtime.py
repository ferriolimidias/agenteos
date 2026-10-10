from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from app.services.followup_engine import (
    FollowupContext,
    _dispatch_followup_execution,
    _upsert_execution_for_rule,
    processar_followups_pendentes,
)
from app.services.followup_rules import FollowupRuleSpec


class FakeResult:
    def __init__(self, *, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.scalar


class FakeSession:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.committed = False
        self.flushed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _stmt):
        if self.results:
            return self.results.pop(0)
        return FakeResult()

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed = True


def make_ids():
    return SimpleNamespace(
        empresa_id=uuid.uuid4(),
        followup_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        conexao_id=uuid.uuid4(),
    )


def make_execution(ids, *, scheduled_at=None):
    now = datetime.utcnow()
    lead = SimpleNamespace(
        id=ids.lead_id,
        empresa_id=ids.empresa_id,
        nome_contato="Teste",
        status_atendimento="aberto",
        bot_pausado_ate=None,
        dados_adicionais={},
    )
    contact = SimpleNamespace(
        id=ids.contact_id,
        phone_e164="+5511999990000",
        external_contact_id="5511999990000",
    )
    conversation = SimpleNamespace(
        id=ids.conversation_id,
        lead_id=ids.lead_id,
        contact_id=ids.contact_id,
        conexao_id=ids.conexao_id,
        provider="meta_whatsapp",
        channel="whatsapp",
        external_conversation_id="conv-test",
        last_inbound_at=now - timedelta(minutes=5),
        last_outbound_at=None,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        empresa_id=ids.empresa_id,
        followup_id=ids.followup_id,
        lead_id=ids.lead_id,
        contact_id=ids.contact_id,
        conversation_id=ids.conversation_id,
        conexao_id=ids.conexao_id,
        provider="meta_whatsapp",
        channel="whatsapp",
        status="scheduled",
        scheduled_at=scheduled_at or now - timedelta(minutes=1),
        triggered_at=now - timedelta(minutes=10),
        idempotency_key="execution-key",
        policy_snapshot_json={},
        outbox_id=None,
        external_message_id=None,
        processing_started_at=None,
        sent_at=None,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        stop_reason=None,
        lead=lead,
        contact=contact,
        conversation=conversation,
    )


class FollowupWorkerRuntimeTest(IsolatedAsyncioTestCase):
    async def test_followup_due_execution_worker_claims_and_uses_fake_provider(self):
        ids = make_ids()
        execution = make_execution(ids)
        rule = SimpleNamespace(
            id=ids.followup_id,
            empresa_id=ids.empresa_id,
            nome="Regra",
            ativo=True,
            objetivo_prompt="Oi",
            tempo_gatilho_minutos=1,
        )
        session = FakeSession([FakeResult(rows=[execution]), FakeResult(rows=[(rule, None)])])
        calls = []

        async def fake_dispatch(_session, *, execution, spec):
            calls.append((execution.id, spec.message_content))
            execution.status = "sent"
            return True

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            with patch("app.services.followup_engine.AsyncSessionLocal", lambda: session), \
                    patch("app.services.followup_engine._dispatch_followup_execution", fake_dispatch), \
                    patch("app.api.main.redis_client", None):
                result = await processar_followups_pendentes()

        self.assertEqual(result, {"enviados": 1, "ignorados": 0, "erros": 0})
        self.assertEqual(len(calls), 1)
        self.assertTrue(session.committed)
        self.assertTrue(any("WORKER_JOB_CLAIM worker=followup claimed=1" in entry for entry in captured.output))
        self.assertTrue(any("WORKER_JOB_SUCCESS worker=followup outcome=sent" in entry for entry in captured.output))

    async def test_followup_future_execution_is_not_sent(self):
        session = FakeSession([FakeResult(rows=[])])
        with patch("app.services.followup_engine.AsyncSessionLocal", lambda: session), \
                patch("app.api.main.redis_client", None):
            result = await processar_followups_pendentes()

        self.assertEqual(result, {"enviados": 0, "ignorados": 0, "erros": 0})
        self.assertTrue(session.committed)

    async def test_worker_exception_moves_execution_to_bounded_retry(self):
        ids = make_ids()
        execution = make_execution(ids)
        rule = SimpleNamespace(
            id=ids.followup_id,
            empresa_id=ids.empresa_id,
            nome="Regra",
            ativo=True,
            objetivo_prompt="Oi",
            tempo_gatilho_minutos=1,
        )
        session = FakeSession([FakeResult(rows=[execution]), FakeResult(rows=[(rule, None)])])

        async def failing_dispatch(*_args, **_kwargs):
            raise RuntimeError("transient worker failure")

        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            with patch("app.services.followup_engine.AsyncSessionLocal", lambda: session), \
                    patch("app.services.followup_engine._dispatch_followup_execution", failing_dispatch), \
                    patch("app.api.main.redis_client", None):
                result = await processar_followups_pendentes()

        self.assertEqual(result["erros"], 1)
        self.assertEqual(execution.status, "retry")
        self.assertEqual(execution.policy_snapshot_json["processing_retry_count"], 1)
        self.assertGreater(execution.scheduled_at, datetime.utcnow())
        self.assertTrue(any("WORKER_JOB_RETRY worker=followup attempt=1" in entry for entry in captured.output))

    async def test_max_processing_retry_logs_terminal_failure(self):
        ids = make_ids()
        execution = make_execution(ids)
        execution.policy_snapshot_json = {"processing_retry_count": 2}
        rule = SimpleNamespace(
            id=ids.followup_id,
            empresa_id=ids.empresa_id,
            nome="Regra",
            ativo=True,
            objetivo_prompt="Oi",
            tempo_gatilho_minutos=1,
        )
        session = FakeSession([FakeResult(rows=[execution]), FakeResult(rows=[(rule, None)])])

        async def failing_dispatch(*_args, **_kwargs):
            raise RuntimeError("transient worker failure")

        with self.assertLogs("uvicorn.error", level="ERROR") as captured:
            with patch("app.services.followup_engine.AsyncSessionLocal", lambda: session), \
                    patch("app.services.followup_engine._dispatch_followup_execution", failing_dispatch), \
                    patch("app.api.main.redis_client", None):
                result = await processar_followups_pendentes()

        self.assertEqual(result["erros"], 1)
        self.assertEqual(execution.status, "failed")
        self.assertEqual(execution.stop_reason, "processing_retry_exhausted")
        self.assertTrue(any("WORKER_JOB_FAILED worker=followup attempts=3" in entry for entry in captured.output))

    async def test_followup_rule_creates_execution(self):
        ids = make_ids()
        ctx = FollowupContext(
            empresa_id=ids.empresa_id,
            lead=SimpleNamespace(id=ids.lead_id),
            contact=SimpleNamespace(id=ids.contact_id),
            conversation=SimpleNamespace(id=ids.conversation_id),
            conexao_id=ids.conexao_id,
            provider="meta_whatsapp",
            channel="whatsapp",
        )
        captured = {}

        async def fake_upsert(_session, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(triggered_at=None, policy_snapshot_json={})

        anchor = datetime(2026, 8, 30, 18, 0, 0)
        spec = FollowupRuleSpec(message_content="Oi", delay_minutes=60)
        with patch("app.services.followup_engine.upsert_followup_execution", fake_upsert):
            await _upsert_execution_for_rule(
                FakeSession(),
                ctx=ctx,
                followup_id=ids.followup_id,
                spec=spec,
                anchor_kind="outbound_message",
                anchor_at=anchor,
            )

        self.assertEqual(captured["followup_id"], ids.followup_id)
        self.assertEqual(captured["scheduled_at"], anchor + timedelta(minutes=60))

    async def test_followup_dispatch_creates_history_realtime_and_uses_canonical_phone(self):
        ids = make_ids()
        execution = make_execution(ids)
        execution.conversation.last_inbound_at = execution.triggered_at - timedelta(minutes=1)
        session = FakeSession([FakeResult(scalar=None)])
        dispatch_calls = []
        broadcasts = []

        async def fake_enqueue(_session, **kwargs):
            return SimpleNamespace(id=uuid.uuid4(), external_message_id=None)

        async def fake_dispatch(_session, *, outbox, identificador_origem, texto, **_kwargs):
            dispatch_calls.append({"phone": identificador_origem, "texto": texto})
            outbox.external_message_id = "wamid.followup-test"
            return True

        class FakeManager:
            async def broadcast_to_empresa(self, empresa_id, payload):
                broadcasts.append((empresa_id, payload))

        decision = SimpleNamespace(
            business_hours_allowed=True,
            csw_active=True,
            csw_expires_at=None,
            fep_active=False,
            fep_expires_at=None,
            send_mode="fixed_text",
            template_required=False,
            eligible=True,
            reason="eligible",
            next_attempt_at=None,
        )
        with patch("app.services.followup_engine.enqueue_message", fake_enqueue), \
                patch("app.services.followup_engine.dispatch_outbox_now", fake_dispatch), \
                patch("app.services.followup_engine.is_ai_blocked", return_value=False), \
                patch("app.services.followup_engine.evaluate_followup_delivery", return_value=decision), \
                patch("app.services.followup_engine.evaluate_message_policy", return_value={"message_mode": "freeform"}), \
                patch("app.services.websocket_manager.manager", FakeManager()):
            spec = FollowupRuleSpec(message_content="Oi", message_mode="fixed_text", cancel_on_inbound=False)
            ok = await _dispatch_followup_execution(session, execution=execution, spec=spec)

        histories = [item for item in session.added if item.__class__.__name__ == "MensagemHistorico"]
        self.assertTrue(ok)
        self.assertEqual(dispatch_calls[0]["phone"], "+5511999990000")
        self.assertTrue(histories)
        self.assertEqual(histories[0].lead_id, ids.lead_id)
        self.assertEqual(histories[0].channel_conversation_id, ids.conversation_id)
        self.assertEqual(histories[0].external_message_id, "wamid.followup-test")
        self.assertEqual(execution.external_message_id, "wamid.followup-test")
        self.assertEqual(histories[0].origem_mensagem, "followup")
        self.assertTrue(broadcasts)
        self.assertEqual(broadcasts[0][1]["message"]["channel_conversation_id"], str(ids.conversation_id))


class FollowupRuntimeSourceContractTest(TestCase):
    def test_followup_startup_gate_is_explicit_and_default_disabled(self):
        source = Path("app/api/main.py").read_text(encoding="utf-8")
        self.assertIn("FOLLOWUP_WORKER_ENABLED", source)
        self.assertIn('env_flag("FOLLOWUP_WORKER_ENABLED", "0")', source)
        self.assertIn('name="followup"', source)
        self.assertNotIn("startup_database_mode.allows_runtime_workers", source)

    def test_followup_cancelled_status_is_not_claimed(self):
        source = Path("app/services/followup_engine.py").read_text(encoding="utf-8")
        claim_start = source.index("async def processar_followups_pendentes")
        claim_start = source.index("select(FollowupExecution)", claim_start)
        claim_end = source.index(".order_by(FollowupExecution.scheduled_at.asc())", claim_start)
        claim_block = source[claim_start:claim_end]

        self.assertIn('FollowupExecution.status.in_(["scheduled", "retry"])', claim_block)
        self.assertNotIn("cancelled", claim_block)

    def test_worker_preloads_all_execution_context_relationships_and_claims_atomically(self):
        source = Path("app/services/followup_engine.py").read_text(encoding="utf-8")
        worker = source[source.index("async def processar_followups_pendentes"):]
        for relationship in ["lead", "contact", "conversation", "conexao", "followup"]:
            self.assertIn(f"selectinload(FollowupExecution.{relationship})", worker)
        self.assertIn("with_for_update(skip_locked=True)", worker)
        self.assertIn("FOLLOWUP_MAX_PROCESSING_RETRIES = 3", source)

    def test_followup_interface_exposes_execution_status(self):
        source = Path("frontend/src/pages/tenant/TenantFollowUps.jsx").read_text(encoding="utf-8")
        for label in ["Agendados", "Próximo envio", "Enviados", "Falhas", "Cancelados", "Último erro"]:
            self.assertIn(label, source)
