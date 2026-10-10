from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.followup_delivery_policy import evaluate_followup_delivery
from app.services.followup_engine import FollowupContext, _generate_contextual_followup_text
from app.services.followup_rules import (
    FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
    FOLLOWUP_MESSAGE_MODE_FIXED_TEXT,
    FOLLOWUP_MESSAGE_MODE_META_TEMPLATE,
    FollowupRuleSpec,
)


class FakeResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class FakeSession:
    def __init__(self, empresa, messages):
        self.results = [FakeResult([empresa]), FakeResult(messages)]

    async def execute(self, _statement):
        return self.results.pop(0)


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, *, system_prompt, messages, tools_context=None):
        self.calls.append({"system_prompt": system_prompt, "messages": messages, "tools_context": tools_context})
        return "Você comentou que quer emagrecer e já treina 3 vezes por semana. Quer que eu te ajude a organizar isso num plano mais claro?"


def conversation(last_inbound_at):
    return SimpleNamespace(last_inbound_at=last_inbound_at)


def execution(**kwargs):
    defaults = {"ad_entry_expires_at": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_time_a_ai_freeform_inside_24h_and_business_hours_is_eligible():
    now = datetime(2026, 8, 19, 14, 5, 0)
    spec = FollowupRuleSpec(
        message_mode=FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
        send_window_enabled=True,
        send_window_start="09:00",
        send_window_end="21:00",
        timezone="America/Sao_Paulo",
    )

    decision = evaluate_followup_delivery(
        spec=spec,
        conversation=conversation(datetime(2026, 8, 19, 13, 0, 0)),
        execution=execution(),
        provider="meta_whatsapp",
        channel="whatsapp",
        now=now,
    )

    assert decision.eligible is True
    assert decision.csw_active is True
    assert decision.business_hours_allowed is True


def test_time_b_outside_hours_reschedules_to_next_start_and_requires_recheck_later():
    now = datetime(2026, 8, 20, 2, 5, 0)
    spec = FollowupRuleSpec(
        message_mode=FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
        send_window_enabled=True,
        send_window_start="09:00",
        send_window_end="21:00",
        timezone="America/Sao_Paulo",
    )

    decision = evaluate_followup_delivery(
        spec=spec,
        conversation=conversation(datetime(2026, 8, 19, 23, 0, 0)),
        execution=execution(),
        provider="meta_whatsapp",
        channel="whatsapp",
        now=now,
    )

    assert decision.eligible is False
    assert decision.reason == "outside_business_hours"
    assert decision.next_attempt_at == datetime(2026, 8, 20, 12, 0, 0)


def test_time_c_ai_freeform_is_blocked_when_customer_service_window_expired():
    now = datetime(2026, 8, 20, 15, 0, 0)
    spec = FollowupRuleSpec(message_mode=FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL)

    decision = evaluate_followup_delivery(
        spec=spec,
        conversation=conversation(datetime(2026, 8, 19, 10, 0, 0)),
        execution=execution(),
        provider="meta_whatsapp",
        channel="whatsapp",
        now=now,
    )

    assert decision.eligible is False
    assert decision.reason == "customer_service_window_expired"
    assert decision.template_required is True


def test_time_d_meta_template_is_allowed_outside_24h_when_business_hours_allow():
    now = datetime(2026, 8, 20, 15, 0, 0)
    spec = FollowupRuleSpec(message_mode=FOLLOWUP_MESSAGE_MODE_META_TEMPLATE, template_id=str(uuid.uuid4()))

    decision = evaluate_followup_delivery(
        spec=spec,
        conversation=conversation(datetime(2026, 8, 19, 10, 0, 0)),
        execution=execution(),
        provider="meta_whatsapp",
        channel="whatsapp",
        now=now,
    )

    assert decision.eligible is True
    assert decision.csw_active is False
    assert decision.template_required is True


def test_legacy_followups_default_to_fixed_text():
    spec = FollowupRuleSpec.from_row(
        SimpleNamespace(id=uuid.uuid4(), empresa_id=uuid.uuid4(), nome="Legado", objetivo_prompt="Oi", ativo=True),
        {"message_free_text": "Oi", "execute_conditions_json": {}, "cancel_conditions_json": {}},
    )

    assert spec.message_mode == FOLLOWUP_MESSAGE_MODE_FIXED_TEXT


@pytest.mark.asyncio
async def test_ai_contextual_generation_includes_context_and_does_not_repeat_answered_question(monkeypatch):
    fake_llm = FakeLLM()

    class FakeOpenAIProvider:
        @classmethod
        def from_config(cls, config):
            return fake_llm

    monkeypatch.setattr("app.services.followup_engine.OpenAIAgentLLM", FakeOpenAIProvider)

    empresa_id = uuid.uuid4()
    lead = SimpleNamespace(id=uuid.uuid4(), empresa_id=empresa_id, nome_contato="Ana", historico_resumo="")
    contact = SimpleNamespace(id=uuid.uuid4())
    conv = SimpleNamespace(id=uuid.uuid4())
    ctx = FollowupContext(
        empresa_id=empresa_id,
        lead=lead,
        contact=contact,
        conversation=conv,
        conexao_id=uuid.uuid4(),
        provider="meta_whatsapp",
        channel="whatsapp",
    )
    empresa = SimpleNamespace(
        id=empresa_id,
        nome_empresa="Empresa Teste",
        nome_agente="Agente",
        modelo_ia="gpt-4o-mini",
        openai_api_key="sk-test",
        credenciais_canais={},
        ia_personalidade="Tom claro e comercial.",
        ia_regras_negocio="Nao invente oferta.",
        atendente_prompt="",
    )
    messages = [
        SimpleNamespace(texto="Quero emagrecer.", from_me=False, criado_em=datetime(2026, 8, 19, 10, 0, 0)),
        SimpleNamespace(texto="Você já está treinando atualmente?", from_me=True, criado_em=datetime(2026, 8, 19, 10, 1, 0)),
        SimpleNamespace(texto="Sim, faço academia 3 vezes na semana.", from_me=False, criado_em=datetime(2026, 8, 19, 10, 2, 0)),
        SimpleNamespace(texto="Então parece que o que falta é organizar melhor...", from_me=True, criado_em=datetime(2026, 8, 19, 10, 3, 0)),
    ]
    spec = FollowupRuleSpec(
        message_mode=FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
        message_content="Continue a conversa do ponto em que parou.",
    )

    output = await _generate_contextual_followup_text(FakeSession(empresa, messages), ctx=ctx, spec=spec)

    serialized_call = str(fake_llm.calls[0])
    assert "Quero emagrecer" in serialized_call
    assert "academia 3 vezes" in serialized_call
    assert "qual é o seu objetivo" not in output.lower()
    assert output.count("?") == 1
    assert output.endswith("?")
