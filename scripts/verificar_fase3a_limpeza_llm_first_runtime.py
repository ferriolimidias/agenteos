from __future__ import annotations

import asyncio
import io
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.llm.openai_provider import OpenAIAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.tools.registry import ToolRegistry
from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FakeResult:
    def __init__(self, first=None, scalar_value=None, all_items=None):
        self._first = first
        self._scalar_value = scalar_value
        self._all_items = list(all_items or [])

    def scalars(self):
        return self

    def first(self):
        return self._first

    def scalar(self):
        return self._scalar_value

    def all(self):
        return self._all_items


class _FakeSession:
    def __init__(self, *, company=None):
        self.company = company
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        if self.company is not None and self.calls == 1:
            return _FakeResult(first=self.company)
        return _FakeResult(first=None, scalar_value=0, all_items=[])

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, sessions):
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
        raise AssertionError("Redis outbound nao deveria ser usado neste caso")


def _company(**overrides):
    base = {"openai_api_key": None, "credenciais_canais": {}, "status_openai": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def _tenant_config(tenant_id="tenant-fase3a", prompt="Prompt Fase 3A"):
    return {
        "empresa_id": tenant_id,
        "nome_empresa": f"Empresa {tenant_id}",
        "nome_agente": "Assistente",
        "modelo_ia": "gpt-4o-mini",
        "modelo_roteador": "gpt-4o-mini",
        "atendente_prompt": prompt,
        "ia_regras_negocio": "Use apenas dados estruturados das tools.",
        "credenciais_canais": {
            "orcamento": {
                "enabled": True,
                "currency": "BRL",
                "services": {
                    "sofa": {
                        "label": "Sofá",
                        "aliases": ["sofa", "sofá"],
                        "pricing_type": "tier_by_measure",
                        "missing_field": "measure",
                        "tiers": [{"max": 1.8, "price": 180}, {"max": 2.5, "price": 220}],
                    }
                },
            }
        },
        "has_ai": True,
    }


def _plan(*tools: str) -> dict:
    return {
        "selected_tools": list(tools),
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "test_injected_plan",
    }


class _FailingPlanner:
    async def plan(self, **kwargs):
        raise AssertionError("Planner nao deveria ser chamado")


class _FailingToolRegistry(ToolRegistry):
    async def run_planned_tools(self, **kwargs):
        raise AssertionError("Tools nao deveriam ser chamadas")


async def _run_gate_without_ai_case() -> None:
    fake_graph = SimpleNamespace(calls=0)

    async def _ainvoke(state, config=None):
        fake_graph.calls += 1
        return {"resposta_final": "nao usar"}

    fake_graph.ainvoke = _ainvoke
    fake_dispatch = AsyncMock()
    sessions = _SessionFactory([_FakeSession(company=None), _FakeSession(company=_company(openai_api_key=None))])
    message = StandardMessage(
        empresa_id="11111111-1111-1111-1111-111111111111",
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="oi",
        is_human_agent=False,
    )
    with ExitStack() as stack:
        stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
        stack.enter_context(patch("app.api.main.redis_client", _FakeRedis()))
        stack.enter_context(patch("app.agents.graph.graph", fake_graph))
        stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
        await processar_bloco_mensagens([message], background_tasks=None)
    assert_true(fake_graph.calls == 0, "Tenant sem IA chamou graph")
    assert_true(fake_dispatch.await_count == 0, "Tenant sem IA disparou resposta")


async def _run_no_intent_router_case() -> None:
    llm = MockAgentLLM(responses=["Posso ajudar."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    assert_true(not (ROOT / "app/agents/routing").exists(), "Diretorio de roteamento antigo ainda existe")
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-fase3a"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-fase3a")],
        )
    )
    assert_true(output.response_text, "Fluxo sem roteador antigo nao respondeu")


async def _run_agent_internal_ai_gate_case() -> None:
    llm = MockAgentLLM(responses=["nao deveria responder"])
    agent = AtendimentoAgent(llm=llm, tool_planner=_FailingPlanner(), tool_registry=_FailingToolRegistry())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-sem-ia", "has_ai": False})  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-sem-ia"),
            messages=[IncomingMessage(text="quero orçamento para sofá", tenant_id="tenant-sem-ia")],
        )
    )
    assert_true(output.response_text == "", "Tenant sem IA recebeu resposta direta do agente")
    assert_true(output.decision.should_respond is False, "Tenant sem IA deveria falhar fechado")
    assert_true(output.metadata.get("blocked_reason") == "tenant_ai_disabled", output.metadata)
    assert_true(len(llm.calls) == 0, "LLM foi chamada com tenant sem IA")


async def _run_direct_flow_ai_gate_case() -> None:
    from app.agents.graph import run_new_agent_flow

    llm = MockAgentLLM(responses=["nao deveria responder"])
    agent = AtendimentoAgent(llm=llm, tool_planner=_FailingPlanner(), tool_registry=_FailingToolRegistry())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-sem-ia", "has_ai": False})  # type: ignore[attr-defined]
    output = await run_new_agent_flow(
        {"empresa_id": "tenant-sem-ia", "mensagem_atual": "quero preço, orçamento, agendar, humano"},
        atendimento_agent=agent,
    )
    assert_true(output.response_text == "", "Fluxo direto gerou resposta com IA desabilitada")
    assert_true(output.decision.should_respond is False, "Fluxo direto nao marcou should_respond=False")
    assert_true(len(llm.calls) == 0, "Fluxo direto chamou LLM com IA desabilitada")


async def _run_planner_registry_case() -> None:
    registry = ToolRegistry()
    state = ConversationState(empresa_id="tenant-fase3a", mensagem_atual="quero orçamento para sofá")
    plan = await MockToolPlanner().plan(
        state=state,
        tenant_config=_tenant_config(),
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
    )
    assert_true(plan["selected_tools"] == ["context"], f"Mock sem plano nao pode selecionar por termo local: {plan}")
    term_plan = await MockToolPlanner().plan(
        state=ConversationState(empresa_id="tenant-fase3a", mensagem_atual="quero preço, orçamento, agendar, humano"),
        tenant_config=_tenant_config(),
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
    )
    assert_true(term_plan["selected_tools"] == ["context"], f"Termos acionaram tools no mock sem plano: {term_plan}")
    injected = await MockToolPlanner(default_plan=_plan("orcamento")).plan(
        state=state,
        tenant_config=_tenant_config(),
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
    )
    assert_true(injected["selected_tools"] == ["orcamento"], f"Plano injetado deve ser preservado exatamente: {injected}")
    injected_agenda = await MockToolPlanner(default_plan=_plan("agenda")).plan(
        state=state,
        tenant_config=_tenant_config(),
        memory={},
        history=[],
        available_tools=registry.available_tools({}),
    )
    assert_true(injected_agenda["selected_tools"] == ["agenda"], f"Plano injetado de agenda deve ser exato: {injected_agenda}")
    no_plan_results = await registry.run_planned_tools(
        state=ConversationState(empresa_id="tenant-fase3a", mensagem_atual="quanto custa um sofá de 1,8 metros?"),
        plan={"selected_tools": ["context"], "tool_args": {}, "expected_missing_fields": [], "intent_summary": ""},
        message=IncomingMessage(text="quanto custa um sofá de 1,8 metros?"),
        tenant_config=_tenant_config(),
    )
    assert_true([item["name"] for item in no_plan_results] == ["context"], "Registry decidiu tool fora do plano")


async def _run_structured_context_and_no_composer_case() -> None:
    llm = MockAgentLLM(responses=["Resposta sem texto legado."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context", "orcamento")))
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]

    async def fake_run_tool(tool_name, **kwargs):
        if tool_name == "context":
            return {
                "name": "context",
                "ok": True,
                "data": {"memory_update": {"slots": {"origem": "NAO VAZAR"}}},
                "missing_fields": [],
                "actions": [],
                "memory_update": {},
                "error": None,
            }
        return await ToolRegistry().run_tool(tool_name, **kwargs)

    assert_true(not (ROOT / "app/agents/atendimento/composer.py").exists(), "Composer antigo ainda existe")
    agent.tool_registry.run_tool = fake_run_tool  # type: ignore[method-assign]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-fase3a"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-fase3a")],
        )
    )
    assert_true("NAO VAZAR" not in output.response_text, "Texto interno vazou")
    assert_true("NAO VAZAR" not in str(llm.calls), "Texto interno entrou no prompt/contexto")


async def _run_budget_repair_case() -> None:
    llm = MockAgentLLM(responses=["Vou calcular e te retorno.", "O orçamento prévio fica em R$ 180. Quer agendar?"])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context", "orcamento")))
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    state = ConversationState(empresa_id="tenant-fase3a")
    output = await agent.run(
        AtendimentoAgentInput(
            state=state,
            messages=[IncomingMessage(text="sofá 1,8 metros", tenant_id="tenant-fase3a")],
        )
    )
    assert_true("R$ 180" in output.response_text, output.response_text)
    assert_true(output.metadata.get("repair_attempted") is True, "Repair loop nao foi acionado")
    assert_true(state.slots.get("service_type") == "sofa", f"Slot service_type nao persistiu: {state.slots}")
    assert_true(float(state.slots.get("measure")) == 1.8, f"Slot measure nao persistiu: {state.slots}")
    assert_true((state.last_budget_estimate or {}).get("amount") == 180.0, state.last_budget_estimate)


async def _run_missing_field_case() -> None:
    llm = MockAgentLLM(responses=["Qual a medida do sofá?"])
    agent = AtendimentoAgent(
        llm=llm,
        tool_planner=MockToolPlanner(plans=[_plan("context", "orcamento"), _plan("context", "agenda")]),
    )
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-fase3a"),
            messages=[IncomingMessage(text="quanto custa um sofá?", tenant_id="tenant-fase3a")],
        )
    )
    lower = output.response_text.lower()
    assert_true("medida" in lower or "tamanho" in lower, output.response_text)
    assert_true("r$" not in lower, "Inventou preco com campo faltante")
    assert_true("endereço" not in lower and "telefone" not in lower, "Perguntou campo fora do missing_fields")


async def _run_split_case() -> None:
    from app.services import channel_factory

    async def fake_sleep(delay):
        return None

    buffer = io.StringIO()
    with patch("asyncio.sleep", fake_sleep), patch("sys.stdout", buffer):
        await channel_factory.despachar_mensagem(
            "canal_teste",
            "destino",
            "Primeiro parágrafo.\n\nSegundo parágrafo.",
            split_messages=False,
        )
    rendered = buffer.getvalue()
    assert_true(rendered.count("Canal desconhecido") == 1, rendered)
    assert_true("Primeiro parágrafo.\n\nSegundo parágrafo." in rendered, "Dispatcher quebrou a resposta IA")


async def _run_shared_memory_next_turn_case() -> None:
    llm = MockAgentLLM(responses=["O orçamento prévio fica em R$ 180.", "Para agendar, me envie nome, endereço ou bairro, melhor dia, melhor turno e telefone."])
    agent = AtendimentoAgent(
        llm=llm,
        tool_planner=MockToolPlanner(plans=[_plan("context", "orcamento"), _plan("context", "agenda")]),
    )
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    state = ConversationState(empresa_id="tenant-fase3a")
    await agent.run(
        AtendimentoAgentInput(state=state, messages=[IncomingMessage(text="sofá 1,8 metros", tenant_id="tenant-fase3a")])
    )
    output = await agent.run(
        AtendimentoAgentInput(state=state, messages=[IncomingMessage(text="Pode agendar", tenant_id="tenant-fase3a")])
    )
    assert_true("nome" in output.response_text.lower(), output.response_text)
    agenda_context = (output.metadata.get("tools_context") or {}).get("agenda") or {}
    assert_true(agenda_context.get("last_budget_estimate"), "Agenda nao recebeu memoria de orcamento")


async def _run_multi_tenant_and_security_case() -> None:
    os.environ["AGENTEOS_LLM_MODE"] = "mock"

    async def fail_openai(*args, **kwargs):
        raise AssertionError("OpenAI real foi chamado")

    tenant_a = _tenant_config("tenant-a", "Prompt exclusivo A")
    tenant_b = _tenant_config("tenant-b", "Prompt exclusivo B")
    llm_a = MockAgentLLM(responses=["Resposta segura A."])
    llm_b = MockAgentLLM(responses=["Resposta segura B."])
    agent_a = AtendimentoAgent(llm=llm_a, tool_planner=MockToolPlanner())
    agent_b = AtendimentoAgent(llm=llm_b, tool_planner=MockToolPlanner())
    agent_a._load_tenant_config = AsyncMock(return_value=tenant_a)  # type: ignore[attr-defined]
    agent_b._load_tenant_config = AsyncMock(return_value=tenant_b)  # type: ignore[attr-defined]

    with patch.object(OpenAIAgentLLM, "generate", new=fail_openai):
        out_a = await agent_a.run(AtendimentoAgentInput(state=ConversationState(empresa_id="tenant-a"), messages=[IncomingMessage(text="oi")]))
        out_b = await agent_b.run(AtendimentoAgentInput(state=ConversationState(empresa_id="tenant-b"), messages=[IncomingMessage(text="oi")]))
    assert_true("Prompt exclusivo A" in llm_a.calls[0]["system_prompt"], "Prompt A ausente")
    assert_true("Prompt exclusivo B" in llm_b.calls[0]["system_prompt"], "Prompt B ausente")
    assert_true("Prompt exclusivo B" not in llm_a.calls[0]["system_prompt"], "Prompt B vazou para A")
    for output in (out_a, out_b):
        for token in ("Intent" + "Router", "Specialist" + "Result", "ToolRegistry", "ConversationState", "sk-", "Bearer"):
            assert_true(token.lower() not in output.response_text.lower(), f"Token tecnico vazou: {token}")


async def main() -> int:
    await _run_gate_without_ai_case()
    await _run_agent_internal_ai_gate_case()
    await _run_direct_flow_ai_gate_case()
    await _run_no_intent_router_case()
    await _run_planner_registry_case()
    await _run_structured_context_and_no_composer_case()
    await _run_budget_repair_case()
    await _run_missing_field_case()
    await _run_shared_memory_next_turn_case()
    await _run_split_case()
    await _run_multi_tenant_and_security_case()
    print("verificar_fase3a_limpeza_llm_first_runtime: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"verificar_fase3a_limpeza_llm_first_runtime: FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1)
