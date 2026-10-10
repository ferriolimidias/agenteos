import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import LLMToolPlanner, MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.tenant_config import build_tenant_agent_config
from app.agents.tools.registry import ToolRegistry
from app.services.anamnese_service import AnamneseLink, AnamneseServiceError
from app.services.tokenized_links import generate_or_get_tokenized_link
from db.models import CRMLead, Empresa


EMPRESA_ID = "11111111-1111-4111-8111-111111111111"
LEAD_ID = "22222222-2222-4222-8222-222222222222"
ANAMNESE_URL = "https://example.com/anamnese/leoft?token=fake-local-token-ag2"


class _FakeDB:
    def add(self, value):
        self.value = value


def _empresa() -> Empresa:
    return Empresa(
        id=uuid.UUID(EMPRESA_ID),
        nome_empresa="LeoFT",
        credenciais_canais={
            "links_tokenizados": {
                "anamnese_leoft": {
                    "ativo": True,
                    "finalidade": "anamnese",
                    "base_url": "https://example.com/anamnese/leoft",
                    "public_slug": "leoft",
                    "parametro_token": "token",
                    "validade_horas": 168,
                    "oferta_nome": "Metodo Hibrido LeoFT",
                }
            }
        },
    )


def _lead() -> CRMLead:
    return CRMLead(
        id=uuid.UUID(LEAD_ID),
        empresa_id=uuid.UUID(EMPRESA_ID),
        nome_contato="Lead Teste",
        dados_adicionais={},
    )


def test_tokenized_anamnese_first_call_creates_and_second_reuses(monkeypatch):
    monkeypatch.setenv("TOKENIZED_LINK_ENCRYPTION_KEY", "test-key-with-at-least-32-characters-123456")
    db = _FakeDB()
    empresa = _empresa()
    lead = _lead()

    first = generate_or_get_tokenized_link(db, empresa, lead, "anamnese_leoft")
    second = generate_or_get_tokenized_link(db, empresa, lead, "anamnese_leoft")

    assert first.reused is False
    assert second.reused is True
    assert first.record["anamnese_id"] == second.record["anamnese_id"]
    assert first.url == second.url
    assert first.token == second.token
    assert first.token not in json.dumps(lead.dados_adicionais)
    assert first.record["token_ciphertext"]


def test_anamnese_tool_is_exposed_only_for_tenant_with_active_config():
    configured = build_tenant_agent_config(_empresa())
    unconfigured = build_tenant_agent_config(Empresa(id=uuid.uuid4(), nome_empresa="Outro", credenciais_canais={}))

    assert configured["anamnese_available"] is True
    assert "anamnese" in ToolRegistry().available_tools(configured)
    assert "links_tokenizados" not in configured["credenciais_canais"]
    assert unconfigured["anamnese_available"] is False
    assert "anamnese" not in ToolRegistry().available_tools(unconfigured)


def test_planner_exposes_semantic_anamnese_contract_and_accepts_contextual_decision():
    llm = MockAgentLLM(
        responses=[json.dumps({
            "selected_tools": ["context", "anamnese"],
            "tool_args": {"anamnese": {}},
            "expected_missing_fields": [],
            "intent_summary": "aceite contextual da avaliacao",
            "commercial_state": {},
        })]
    )
    planner = LLMToolPlanner(llm=llm)
    state = ConversationState(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        mensagem_atual="Sim, pode mandar",
        historico_curto=["Assistente: Posso te mandar a avaliacao?", "Usuario: Sim, pode mandar"],
        last_agent_question="Posso te mandar a avaliacao?",
    )

    with patch("app.agents.planner.tool_planner._load_commercial_context", AsyncMock(return_value=([], []))):
        plan = asyncio.run(planner.plan(
            state=state,
            tenant_config={"empresa_id": EMPRESA_ID},
            memory={"last_agent_question": state.last_agent_question},
            history=[
                {"role": "assistant", "content": "Posso te mandar a avaliacao?"},
                {"role": "user", "content": "Sim, pode mandar"},
            ],
            available_tools=["context", "anamnese"],
    ))

    assert "anamnese" in plan["selected_tools"]
    schemas = llm.calls[0]["messages"][0]["content"]
    assert "semantic_precondition" in schemas
    assert '"selected_tools": []' in schemas


def test_integrated_agent_handoff_includes_url_and_calls_outbound_mock_once(monkeypatch):
    created = AnamneseLink(
        success=True,
        created=True,
        reused=False,
        anamnese_id=f"anamnese_leoft:{LEAD_ID}",
        url=ANAMNESE_URL,
        link_slug="anamnese_leoft",
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        conversation_id=None,
    )
    create_mock = AsyncMock(return_value=created)
    monkeypatch.setattr("app.agents.tools.anamnese_tool.criar_ou_obter_anamnese", create_mock)
    planner = MockToolPlanner(default_plan={
        "selected_tools": ["context", "anamnese"],
        "tool_args": {"anamnese": {}},
        "expected_missing_fields": [],
        "intent_summary": "aceite contextual da avaliacao",
        "commercial_state": {},
    })
    agent = AtendimentoAgent(
        llm=MockAgentLLM(responses=["Perfeito. Aqui esta tua avaliacao:"]),
        tool_planner=planner,
    )
    agent._load_tenant_config = AsyncMock(return_value={
        "empresa_id": EMPRESA_ID,
        "has_ai": True,
        "nome_empresa": "LeoFT",
        "anamnese_available": True,
        "credenciais_canais": {},
    })
    outbound = AsyncMock(return_value={"sent": True})
    output = asyncio.run(agent.run(AtendimentoAgentInput(
        state=ConversationState(
            empresa_id=EMPRESA_ID,
            lead_id=LEAD_ID,
            mensagem_atual="Sim, pode mandar",
            historico_curto=["Assistente: Posso te mandar a avaliacao?"],
        ),
        messages=[IncomingMessage(text="Sim, pode mandar", tenant_id=EMPRESA_ID, lead_id=LEAD_ID)],
    )))
    asyncio.run(outbound(output.response_text))

    assert create_mock.await_count == 1
    assert output.response_text.count(ANAMNESE_URL) == 1
    assert output.metadata["tool_plan"]["selected_tools"] == ["context", "anamnese"]
    assert "token=%2A%2A%2A" in output.metadata["tools_context"]["anamnese"]["url"]
    assert "fake-local-token-ag2" not in json.dumps(output.metadata)
    outbound.assert_awaited_once_with(output.response_text)


def test_anamnese_tool_error_does_not_invent_or_dispatch_url(monkeypatch):
    monkeypatch.setattr(
        "app.agents.tools.anamnese_tool.criar_ou_obter_anamnese",
        AsyncMock(side_effect=AnamneseServiceError("falha controlada")),
    )
    agent = AtendimentoAgent(
        llm=MockAgentLLM(responses=["nao deve ser usada"]),
        tool_planner=MockToolPlanner(default_plan={
            "selected_tools": ["anamnese"],
            "tool_args": {},
            "expected_missing_fields": [],
            "intent_summary": "aceite",
            "commercial_state": {},
        }),
    )
    agent._load_tenant_config = AsyncMock(return_value={
        "empresa_id": EMPRESA_ID,
        "has_ai": True,
        "anamnese_available": True,
    })
    output = asyncio.run(agent.run(AtendimentoAgentInput(
        state=ConversationState(empresa_id=EMPRESA_ID, lead_id=LEAD_ID),
        messages=[IncomingMessage(text="Sim", tenant_id=EMPRESA_ID, lead_id=LEAD_ID)],
    )))

    assert output.response_text == ""
    assert output.metadata["blocked_reason"] == "anamnese_tool_error"
    assert output.guard_result.reason_codes == ["anamnese_tool_error"]


@pytest.mark.parametrize("error_class", [
    type("GraphFailure", (RuntimeError,), {}),
    type("ToolFailure", (RuntimeError,), {}),
    type("OutboundFailure", (RuntimeError,), {}),
])
def test_processing_wrapper_clears_processing_on_every_error_layer(error_class):
    from app.api import utils

    lead = SimpleNamespace(id=LEAD_ID, telefone_contato="***", ia_ativa=True, status_atendimento="aberto")
    gate = SimpleNamespace(as_dict=lambda: {})

    async def failing_impl(messages, background_tasks=None):
        lifecycle = utils._PROCESSING_LIFECYCLE.get()
        lifecycle.observe(
            empresa_id=EMPRESA_ID,
            lead=lead,
            ai_gate=gate,
            processing_state="processing",
        )
        raise error_class("controlled")

    terminal = AsyncMock()
    with patch.object(utils, "_processar_bloco_mensagens_impl", side_effect=failing_impl), patch.object(
        utils, "_broadcast_ai_state_update", terminal
    ):
        with pytest.raises(error_class):
            asyncio.run(utils.processar_bloco_mensagens([SimpleNamespace()]))

    assert terminal.await_count == 1
    assert terminal.await_args.kwargs["override"]["processing_state"] == "failed_start"


def test_processing_wrapper_does_not_duplicate_existing_success_terminal():
    from app.api import utils

    lead = SimpleNamespace(id=LEAD_ID)
    gate = SimpleNamespace(as_dict=lambda: {})

    async def successful_impl(messages, background_tasks=None):
        lifecycle = utils._PROCESSING_LIFECYCLE.get()
        lifecycle.observe(empresa_id=EMPRESA_ID, lead=lead, ai_gate=gate, processing_state="processing")
        lifecycle.observe(empresa_id=EMPRESA_ID, lead=lead, ai_gate=gate, processing_state="completed")
        return "ok"

    terminal = AsyncMock()
    with patch.object(utils, "_processar_bloco_mensagens_impl", side_effect=successful_impl), patch.object(
        utils, "_broadcast_ai_state_update", terminal
    ):
        result = asyncio.run(utils.processar_bloco_mensagens([SimpleNamespace()]))

    assert result == "ok"
    terminal.assert_not_awaited()
