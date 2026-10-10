from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from app.services.followup_engine import (
    FollowupContext,
    registrar_followup_message_event,
    registrar_followup_tag_event,
)
from app.services.followup_rules import (
    FOLLOWUP_ANCHOR_FIXED_DATETIME,
    FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
    FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
    FOLLOWUP_MODE_CONVERSATION_RESUME,
    FOLLOWUP_MODE_SCHEDULED_MESSAGE,
    FollowupRuleSpec,
)


def make_payload(**kwargs):
    payload = SimpleNamespace(**kwargs)
    payload.model_fields_set = set(kwargs)
    return payload


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FollowupRuleSpecSimplificationTests(TestCase):
    def test_conversation_resume_defaults_to_last_agent_anchor(self):
        payload = make_payload(
            nome="Retomar conversa",
            mode=FOLLOWUP_MODE_CONVERSATION_RESUME,
            delay_value=3,
            delay_unit="hours",
            message_content="Olá {{nome}}",
            cancel_tag_ids=["tag-a", "tag-a", " "],
        )

        spec = FollowupRuleSpec.from_payload(payload)

        self.assertEqual(spec.mode, FOLLOWUP_MODE_CONVERSATION_RESUME)
        self.assertEqual(spec.anchor_type, FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE)
        self.assertEqual(spec.delay_minutes, 180)
        self.assertTrue(spec.cancel_on_inbound)
        self.assertEqual(spec.cancel_tag_ids, ("tag-a",))

    def test_scheduled_message_keeps_inbound_cancel_optional(self):
        payload = make_payload(
            nome="Convite",
            mode=FOLLOWUP_MODE_SCHEDULED_MESSAGE,
            delay_value=24,
            delay_unit="hours",
            anchor_type=FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
            message_content="Convite automático",
            cancel_on_inbound=False,
        )

        spec = FollowupRuleSpec.from_payload(payload)

        self.assertEqual(spec.mode, FOLLOWUP_MODE_SCHEDULED_MESSAGE)
        self.assertEqual(spec.anchor_type, FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE)
        self.assertEqual(spec.delay_minutes, 24 * 60)
        self.assertFalse(spec.cancel_on_inbound)
        self.assertTrue(spec.cancel_on_event_end)

    def test_storage_payload_keeps_simplified_fields(self):
        spec = FollowupRuleSpec(
            mode=FOLLOWUP_MODE_SCHEDULED_MESSAGE,
            delay_minutes=90,
            anchor_type=FOLLOWUP_ANCHOR_FIXED_DATETIME,
            message_content="Mensagem",
            cancel_on_inbound=False,
            cancel_on_human=True,
            cancel_on_opt_out=True,
            cancel_on_event_end=True,
            cancel_tag_ids=("tag-1", "tag-2"),
            activation_at=datetime(2026, 7, 25, 12, 0, 0),
            campaign_or_event_id="campanha-1",
            needs_review=True,
            trigger_tag_id="tag-1",
        )

        storage = spec.to_storage_payload()

        self.assertEqual(storage["delay_minutes"], 90)
        self.assertEqual(storage["execute_conditions_json"]["mode"], FOLLOWUP_MODE_SCHEDULED_MESSAGE)
        self.assertEqual(storage["execute_conditions_json"]["anchor_type"], FOLLOWUP_ANCHOR_FIXED_DATETIME)
        self.assertEqual(storage["cancel_conditions_json"]["cancel_tag_ids"], ["tag-1", "tag-2"])
        self.assertTrue(storage["cancel_conditions_json"]["cancel_on_opt_out"])
        self.assertEqual(storage["execute_conditions_json"]["campaign_or_event_id"], "campanha-1")


class FollowupEngineBehaviorTests(IsolatedAsyncioTestCase):
    async def test_conversation_resume_outbound_reanchors_latest_message(self):
        empresa_id = uuid.uuid4()
        lead = SimpleNamespace(id=uuid.uuid4(), empresa_id=empresa_id, dados_adicionais={})
        contact = SimpleNamespace(id=uuid.uuid4())
        conversation = SimpleNamespace(id=uuid.uuid4(), last_outbound_at=utcnow_naive(), last_inbound_at=None)
        ctx = FollowupContext(
            empresa_id=empresa_id,
            lead=lead,
            contact=contact,
            conversation=conversation,
            conexao_id=uuid.uuid4(),
            provider="meta_whatsapp",
            channel="whatsapp",
        )
        spec = FollowupRuleSpec(
            mode=FOLLOWUP_MODE_CONVERSATION_RESUME,
            delay_minutes=180,
            anchor_type=FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
            message_content="Olá",
        )
        item = SimpleNamespace(id=uuid.uuid4(), ativo=True, nome="Retomar", criado_em=utcnow_naive())
        session = AsyncMock()
        session.execute = AsyncMock()

        with patch("app.services.followup_engine._resolve_followup_context", AsyncMock(return_value=ctx)), patch(
            "app.services.followup_engine._load_followup_rows",
            AsyncMock(return_value=[(item, None, spec)]),
        ), patch(
            "app.services.followup_engine._cancel_existing_pending",
            AsyncMock(return_value=1),
        ) as cancel_mock, patch(
            "app.services.followup_engine._upsert_execution_for_rule",
            AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ) as upsert_mock:
            result = await registrar_followup_message_event(
                session,
                empresa_id=empresa_id,
                lead=lead,
                from_me=True,
                message=SimpleNamespace(id=uuid.uuid4(), external_message_id="msg-1"),
                message_at=utcnow_naive(),
            )

        self.assertEqual(result, {"scheduled": 1, "cancelled": 1})
        cancel_mock.assert_awaited_once()
        self.assertEqual(cancel_mock.await_args.kwargs["reason"], "outbound_replaced")
        upsert_mock.assert_awaited_once()
        session.execute.assert_not_awaited()

    async def test_scheduled_message_inbound_cancels_when_response_arrives(self):
        empresa_id = uuid.uuid4()
        lead = SimpleNamespace(id=uuid.uuid4(), empresa_id=empresa_id, dados_adicionais={})
        contact = SimpleNamespace(id=uuid.uuid4())
        conversation = SimpleNamespace(id=uuid.uuid4(), last_outbound_at=None, last_inbound_at=utcnow_naive())
        ctx = FollowupContext(
            empresa_id=empresa_id,
            lead=lead,
            contact=contact,
            conversation=conversation,
            conexao_id=uuid.uuid4(),
            provider="meta_whatsapp",
            channel="whatsapp",
        )
        spec = FollowupRuleSpec(
            mode=FOLLOWUP_MODE_SCHEDULED_MESSAGE,
            delay_minutes=60,
            anchor_type=FOLLOWUP_ANCHOR_FIXED_DATETIME,
            message_content="Convite",
            cancel_on_inbound=True,
        )
        item = SimpleNamespace(id=uuid.uuid4(), ativo=True, nome="Convite", criado_em=utcnow_naive())
        session = AsyncMock()

        with patch("app.services.followup_engine._resolve_followup_context", AsyncMock(return_value=ctx)), patch(
            "app.services.followup_engine._load_followup_rows",
            AsyncMock(return_value=[(item, None, spec)]),
        ), patch(
            "app.services.followup_engine._cancel_existing_pending",
            AsyncMock(return_value=1),
        ) as cancel_mock, patch(
            "app.services.followup_engine._upsert_execution_for_rule",
            AsyncMock()
        ) as upsert_mock:
            result = await registrar_followup_message_event(
                session,
                empresa_id=empresa_id,
                lead=lead,
                from_me=False,
                message=SimpleNamespace(id=uuid.uuid4(), external_message_id="msg-2"),
                message_at=utcnow_naive(),
            )

        self.assertEqual(result, {"scheduled": 0, "cancelled": 1})
        cancel_mock.assert_awaited_once()
        self.assertEqual(cancel_mock.await_args.kwargs["reason"], "inbound_received")
        upsert_mock.assert_not_awaited()
        session.execute.assert_not_awaited()

    async def test_tag_application_cancels_matching_pending_followups(self):
        empresa_id = uuid.uuid4()
        lead = SimpleNamespace(id=uuid.uuid4(), empresa_id=empresa_id, dados_adicionais={})
        contact = SimpleNamespace(id=uuid.uuid4())
        conversation = SimpleNamespace(id=uuid.uuid4(), last_outbound_at=None, last_inbound_at=None)
        ctx = FollowupContext(
            empresa_id=empresa_id,
            lead=lead,
            contact=contact,
            conversation=conversation,
            conexao_id=uuid.uuid4(),
            provider="meta_whatsapp",
            channel="whatsapp",
        )
        tag_id = uuid.uuid4()
        spec = FollowupRuleSpec(
            mode=FOLLOWUP_MODE_SCHEDULED_MESSAGE,
            delay_minutes=30,
            anchor_type=FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
            message_content="Aguardar",
            cancel_tag_ids=(str(tag_id),),
        )
        item = SimpleNamespace(id=uuid.uuid4(), ativo=True, nome="Tag canceladora", criado_em=utcnow_naive())
        session = AsyncMock()

        with patch("app.services.followup_engine._resolve_followup_context", AsyncMock(return_value=ctx)), patch(
            "app.services.followup_engine._load_followup_rows",
            AsyncMock(return_value=[(item, None, spec)]),
        ), patch(
            "app.services.followup_engine._cancel_existing_pending",
            AsyncMock(return_value=1),
        ) as cancel_mock, patch(
            "app.services.followup_engine._upsert_execution_for_rule",
            AsyncMock()
        ) as upsert_mock:
            result = await registrar_followup_tag_event(
                session,
                empresa_id=empresa_id,
                lead=lead,
                tag_id=tag_id,
                action="applied",
                occurred_at=utcnow_naive(),
            )

        self.assertEqual(result, {"scheduled": 0, "cancelled": 1})
        cancel_mock.assert_awaited_once()
        self.assertEqual(cancel_mock.await_args.kwargs["reason"], "cancel_tag_applied")
        upsert_mock.assert_not_awaited()
        session.execute.assert_not_awaited()
