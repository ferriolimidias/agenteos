from __future__ import annotations

import asyncio
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.atendimento.formatters import format_for_whatsapp
from app.agents.llm.mock import MockAgentLLM
from app.agents.llm.openai_provider import OpenAIAgentLLM
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage
from app.agents.tools.registry import ToolRegistry
from app.agents.planner.tool_planner import MockToolPlanner


FORBIDDEN = (
    "Intent" + "Router",
    "Specialist" + "Result",
    "ToolRegistry",
    "ConversationState",
    "sk-",
    "Bearer",
    "{",
    "}",
)


def _plan(*tools: str) -> dict:
    return {
        "selected_tools": list(tools),
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "test_injected_plan",
    }


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FakeResult:
    def __init__(self, first=None, scalar_value=None):
        self._first = first
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def first(self):
        return self._first

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    def __init__(self, *, company=None):
        self.company = company
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        if self.company is not None and self.calls == 1:
            return _FakeResult(first=self.company)
        return _FakeResult(first=None, scalar_value=0)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, sessions: list[_FakeSession]):
        self.sessions = list(sessions)
        self.index = 0

    def __call__(self):
        if self.index >= len(self.sessions):
            return _FakeSession()
        session = self.sessions[self.index]
        self.index += 1
        return session


class _FakeRedis:
    async def get(self, key):
        return None

    async def setnx(self, key, value):
        return True

    async def expire(self, key, ttl):
        return True

    async def delete(self, key):
        return True

    async def setex(self, key, ttl, value):
        return True


def _company(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "nome_empresa": "Empresa Mock",
        "nome_agente": "Atendente Mock",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "mensagem_saudacao": "Olá, posso ajudar.",
        "ia_personalidade": "Curto, objetivo e cordial.",
        "ia_regras_negocio": "Não inventar preços e usar tools internas.",
        "atendente_prompt": "Responder em português do Brasil.",
        "condutor_prompt": "Se faltar dado, pedir o mínimo necessário.",
        "credenciais_canais": {},
        "openai_api_key": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def _run_gate_case() -> None:
    fake_graph = SimpleNamespace(calls=0)

    async def _ainvoke(state, config=None):
        fake_graph.calls += 1
        return {"resposta_final": "nao deveria ser usado"}

    fake_graph.ainvoke = _ainvoke
    fake_dispatch = AsyncMock()
    fake_redis = _FakeRedis()
    sessions = _SessionFactory([
        _FakeSession(company=None),
        _FakeSession(company=_company(openai_api_key=None)),
    ])

    message = StandardMessage(
        empresa_id="11111111-1111-1111-1111-111111111111",
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="oi",
        is_human_agent=False,
    )

    with ExitStack() as stack:
        stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
        stack.enter_context(patch("app.api.main.redis_client", fake_redis))
        stack.enter_context(patch("app.agents.graph.graph", fake_graph))
        stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
        await processar_bloco_mensagens([message], background_tasks=None)

    assert_true(fake_graph.calls == 0, "Gate sem IA não deveria chamar graph.ainvoke")
    assert_true(fake_dispatch.await_count == 0, "Gate sem IA não deveria disparar resposta")


async def _run_llm_mock_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-mock",
        "nome_empresa": "Tenant Mock",
        "nome_agente": "Assistente Tenant",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "mensagem_saudacao": "Olá!",
        "ia_identidade": "Você é um assistente objetivo.",
        "ia_regras_negocio": "Nunca invente preços.",
        "atendente_prompt": "Seja claro e breve.",
        "condutor_prompt": "Use tools internas quando necessário.",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    llm = MockAgentLLM(responses=["Claro, posso te ajudar."])
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-mock"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-mock")],
        )
    )
    assert_true(llm.calls, "LLM mock não foi chamado")
    assert_true("posso te ajudar" in output.response_text.lower(), "Resposta final não veio do mock")


async def _run_history_case() -> None:
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: "OK")
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-history", "has_ai": True})  # type: ignore[attr-defined]
    state = ConversationState(
        empresa_id="tenant-history",
        historico_curto=[
            "Usuario: oi",
            "Assistente: olá",
            "Usuario: quero agendar",
        ],
    )
    await agent.run(
        AtendimentoAgentInput(
            state=state,
            messages=[IncomingMessage(text="quanto fica?", tenant_id="tenant-history")],
        )
    )
    history = llm.calls[0]["messages"]
    assert_true(len(history) >= 4, f"Histórico curto foi colapsado: {history}")
    assert_true(history[0]["role"] == "user" and history[0]["content"] == "oi", history)
    assert_true(history[1]["role"] == "assistant" and history[1]["content"] == "olá", history)
    assert_true(history[2]["role"] == "user" and history[2]["content"] == "quero agendar", history)
    assert_true(history[-1]["role"] == "user" and history[-1]["content"] == "quanto fica?", history)


async def _run_structured_tool_payload_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-structured",
        "nome_empresa": "Tenant Structured",
        "nome_agente": "Assistente Structured",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "atendente_prompt": "Responda sem vazar internals.",
        "ia_regras_negocio": "Sem preço inventado.",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    llm = MockAgentLLM(responses=["Resposta controlada pelo mock."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context")))
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]

    async def fake_run_tool(tool_name, **kwargs):
        return {
            "name": "context",
            "ok": True,
            "data": {
                "memory_update": {"slots": {"origem": "teste_estruturado"}},
            },
            "missing_fields": [],
            "actions": [],
            "memory_update": {},
            "error": None,
        }

    agent.tool_registry.run_tool = fake_run_tool  # type: ignore[method-assign]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-structured"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-structured")],
        )
    )

    assert_true("teste_estruturado" not in output.response_text, "Atualizacao de memoria vazou para resposta final")
    assert_true("teste_estruturado" not in str(llm.calls), "Atualizacao de memoria entrou no contexto da LLM")
    assert_true(output.response_text == "Resposta controlada pelo mock.", "Resposta final não foi controlada pelo mock")


async def _run_compose_not_called_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-compose",
        "nome_empresa": "Tenant Compose",
        "nome_agente": "Assistente Compose",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    llm = MockAgentLLM(responses=["Resposta do mock."])
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    assert_true(not (ROOT / "app/agents/atendimento/composer.py").exists(), "Composer antigo ainda existe")
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-compose"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-compose")],
        )
    )

    assert_true(output.response_text == "Resposta do mock.", "Resposta inesperada no caminho LLM-first")


async def _run_budget_tool_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-budget",
        "nome_empresa": "Tenant Budget",
        "nome_agente": "Assistente Budget",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "ia_regras_negocio": "Não invente preços.",
        "atendente_prompt": "Use o contexto da tool.",
        "credenciais_canais": {
            "orcamento": {
                "enabled": True,
                "currency": "R$",
                "services": {
                    "sofa": {
                        "label": "Sofá",
                        "aliases": ["sofa", "sofá"],
                        "pricing_type": "tier_by_measure",
                        "missing_field": "tamanho do sofá em metros",
                        "tiers": [
                            {"max": 1.8, "price": 180},
                            {"max": 2.3, "price": 200},
                        ],
                    }
                },
            }
        },
        "has_ai": True,
    }
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: f"Mock usa contexto: {tools_context['orcamento']['estimate']['amount']}")
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context", "orcamento")))
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-budget"),
            messages=[IncomingMessage(text="sofá 2 metros", tenant_id="tenant-budget")],
        )
    )
    assert_true(llm.calls, "LLM mock não registrou chamada")
    assert_true("200" in output.response_text, f"Resposta final não usou contexto do orçamento: {output.response_text}")
    call = llm.calls[0]
    assert_true("orcamento" in call["tools_context"], "Contexto de orçamento não foi enviado ao mock")


async def _run_agenda_tool_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-agenda",
        "nome_empresa": "Tenant Agenda",
        "nome_agente": "Assistente Agenda",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "atendente_prompt": "Peça os dados faltantes.",
        "ia_regras_negocio": "Sem promessa indevida.",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: f"Faltam: {', '.join(tools_context['agenda']['missing_fields'])}")
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context", "agenda")))
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    state = ConversationState(
        empresa_id="tenant-agenda",
        active_flow="orcamento",
        last_budget_estimate={"amount": 200, "total": 200, "currency": "BRL"},
        slots={"service_type": "sofa"},
    )
    output = await agent.run(
        AtendimentoAgentInput(
            state=state,
            messages=[IncomingMessage(text="Pode agendar", tenant_id="tenant-agenda")],
        )
    )
    assert_true("nome" in output.response_text.lower(), f"Agenda não pediu dados faltantes: {output.response_text}")
    assert_true(llm.calls[0]["tools_context"]["agenda"]["missing_fields"], "Agenda tool não retornou missing_fields")


async def _run_image_budget_case() -> None:
    registry = ToolRegistry()
    state_image_only = ConversationState(empresa_id="tenant-image", has_image=True, metadata={"has_image": True}, mensagem_atual="segue a foto")
    selected_image_only = (await MockToolPlanner().plan(
        state=state_image_only,
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
        tenant_config={"credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}}},
    ))["selected_tools"]
    assert_true("orcamento" not in selected_image_only, f"Imagem sem intenção não deve acionar orçamento: {selected_image_only}")

    state_budget_image = ConversationState(empresa_id="tenant-image", has_image=True, metadata={"has_image": True}, mensagem_atual="quanto fica? segue a foto")
    selected_budget_image = (await MockToolPlanner(default_plan=_plan("context", "orcamento")).plan(
        state=state_budget_image,
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
        tenant_config={"credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}}},
    ))["selected_tools"]
    assert_true("orcamento" in selected_budget_image, f"Plano explícito deve permitir orçamento: {selected_budget_image}")


def _formatter_does_not_replace_agenda_text() -> None:
    state = ConversationState(nome="Cliente Teste")
    draft = "Para seguir, preciso que você me envie nome, endereço ou bairro e melhor dia."
    formatted = format_for_whatsapp(draft, state)
    normalized = formatted.replace("*", "").lower()
    assert_true("perfeito. para solicitar o agendamento da visita" not in normalized, formatted)
    assert_true("me envie" in normalized, formatted)
    assert_true("endereço ou bairro" in normalized, formatted)
    assert_true("melhor dia" in normalized, formatted)


async def _run_prompt_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-prompt-a",
        "nome_empresa": "Tenant Prompt A",
        "nome_agente": "Assistente A",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "ia_regras_negocio": "Regras A",
        "atendente_prompt": "Prompt A",
        "condutor_prompt": "Condutor A",
        "credenciais_canais": {},
        "has_ai": True,
    }
    llm = MockAgentLLM(responses=["ok"])
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-prompt-a"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-prompt-a")],
        )
    )
    prompt = llm.calls[0]["system_prompt"]
    assert_true("Prompt A" in prompt, "atendente_prompt não foi incluído no prompt")
    assert_true("Regras A" in prompt, "ia_regras_negocio não foi incluído no prompt")
    assert_true("Assistente A" in prompt, "nome_agente não foi incluído no prompt")
    assert_true("sk-" not in prompt, "Chave vazou para o prompt")


async def _run_security_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-sec",
        "nome_empresa": "Tenant Sec",
        "nome_agente": "Assistente Sec",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "ia_regras_negocio": "Regras sec",
        "atendente_prompt": "Prompt sec",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    llm = MockAgentLLM(responses=["Resposta segura sem marcadores técnicos."])
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-sec"),
            messages=[IncomingMessage(text="quanto custa um sofá de 2 metros?", tenant_id="tenant-sec")],
        )
    )
    for token in FORBIDDEN:
        assert_true(token.lower() not in output.response_text.lower(), f"Resposta vazou token proibido {token!r}: {output.response_text}")
    assert_true(not re.search(r"\b\{\s*\".*\"\s*\}\b", output.response_text), "Resposta trouxe JSON bruto")


async def _run_multi_tenant_case() -> None:
    tenant_a = {
        "empresa_id": "tenant-a",
        "nome_empresa": "Tenant A",
        "nome_agente": "Assistente A",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "atendente_prompt": "Prompt único A",
        "ia_regras_negocio": "Regras A",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
    }
    tenant_b = {
        "empresa_id": "tenant-b",
        "nome_empresa": "Tenant B",
        "nome_agente": "Assistente B",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "atendente_prompt": "Prompt único B",
        "ia_regras_negocio": "Regras B",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"cadeira": {"label": "Cadeira"}}}},
        "has_ai": True,
    }

    llm_a = MockAgentLLM(responses=["A"])
    llm_b = MockAgentLLM(responses=["B"])
    agent_a = AtendimentoAgent(llm=llm_a)
    agent_b = AtendimentoAgent(llm=llm_b)
    agent_a._load_tenant_config = AsyncMock(return_value=tenant_a)  # type: ignore[attr-defined]
    agent_b._load_tenant_config = AsyncMock(return_value=tenant_b)  # type: ignore[attr-defined]

    await agent_a.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-a"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-a")],
        )
    )
    await agent_b.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-b"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-b")],
        )
    )
    prompt_a = llm_a.calls[0]["system_prompt"]
    prompt_b = llm_b.calls[0]["system_prompt"]
    assert_true("Prompt único A" in prompt_a, "Prompt do tenant A não foi preservado")
    assert_true("Prompt único B" in prompt_b, "Prompt do tenant B não foi preservado")
    assert_true("Prompt único B" not in prompt_a, "Prompt do tenant B vazou para A")
    assert_true("Prompt único A" not in prompt_b, "Prompt do tenant A vazou para B")


async def _run_no_real_openai_case() -> None:
    tenant_config = {
        "empresa_id": "tenant-real-off",
        "nome_empresa": "Tenant Real Off",
        "nome_agente": "Assistente Real Off",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "credenciais_canais": {"orcamento": {"enabled": True, "services": {"sofa": {"label": "Sofá"}}}},
        "has_ai": True,
        "_openai_api_key": "sk-test",
    }
    llm = MockAgentLLM(responses=["Mock sem OpenAI real."])
    agent = AtendimentoAgent(llm=llm)
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]

    async def fail_generate(*args, **kwargs):
        raise AssertionError("OpenAIAgentLLM.generate não deveria ser chamado")

    with patch.object(OpenAIAgentLLM, "generate", new=fail_generate):
        output = await agent.run(
            AtendimentoAgentInput(
                state=ConversationState(empresa_id="tenant-real-off"),
                messages=[IncomingMessage(text="oi", tenant_id="tenant-real-off")],
            )
        )

    assert_true("Mock sem OpenAI real." == output.response_text, "Resposta final não veio do mock")


async def main() -> int:
    await _run_gate_case()
    await _run_llm_mock_case()
    await _run_history_case()
    await _run_structured_tool_payload_case()
    await _run_compose_not_called_case()
    await _run_budget_tool_case()
    await _run_agenda_tool_case()
    await _run_image_budget_case()
    await _run_prompt_case()
    await _run_security_case()
    await _run_multi_tenant_case()
    await _run_no_real_openai_case()
    _formatter_does_not_replace_agenda_text()
    print("verificar_fase2_llm_first_mock: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"verificar_fase2_llm_first_mock: FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1)
