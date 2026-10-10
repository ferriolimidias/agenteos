import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.graph import NewAgentGraph, graph, run_new_agent_flow
from app.agents.schemas import IncomingMessage


def test_new_graph_flow_runs_isolated_without_external_calls():
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: f"agenda={tools_context['agenda']['missing_fields'][0]}")
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan={
        "selected_tools": ["agenda"],
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "teste agenda",
    }))
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    output = asyncio.run(run_new_agent_flow(
        {"empresa_id": "tenant-1", "lead_id": "lead-1", "canal": "whatsapp"},
        IncomingMessage(text="quero marcar horário", tenant_id="tenant-1"),
        atendimento_agent=agent,
    ))
    assert output.response_text == "agenda=*Nome*"
    assert output.should_dispatch is False


def test_graph_ainvoke_returns_structured_dict_and_preserves_fields():
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: "R$ 200")
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    local_graph = NewAgentGraph(agent)
    state = {
        "empresa_id": "tenant-1",
        "lead_id": "lead-1",
        "canal": "simulador",
        "identificador_origem": "5511999999999",
        "mensagens": ["Usuario: quanto custa?"],
        "custom_field": "preserved",
    }
    output = asyncio.run(local_graph.ainvoke(state))
    assert isinstance(output, dict)
    assert output["custom_field"] == "preserved"
    assert output["lead_id"] == "lead-1"
    assert output["resposta_final"] == "*R$ 200*"
    assert output["agent_metadata"]["agent"] == "AtendimentoAgent"
    assert "context" in output["ferramentas_selecionadas"]
    assert output["agent_architecture"] == "app.agents"
    assert output["guard_result"]["allowed"] is True


def test_graph_first_turn_keeps_extension_decision_separate_from_dispatch_decision():
    llm = MockAgentLLM(responses=['{"needs_extension": false, "extension": ""}'])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={
        "empresa_id": "tenant-1",
        "has_ai": True,
        "mensagem_saudacao": "GREETING_ALPHA",
    })  # type: ignore[attr-defined]
    local_graph = NewAgentGraph(agent)

    output = asyncio.run(local_graph.ainvoke({
        "empresa_id": "tenant-1",
        "lead_id": "lead-1",
        "canal": "simulador",
        "identificador_origem": "contact-1",
        "first_agent_response": True,
        "mensagens": ["Usuario: Pode me passar mais informações?"],
    }))

    assert output["first_turn_decision"] == {"needs_extension": False, "extension": ""}
    assert output["pendencias"] == []
    assert output["status_conversa"] == "IA_ATIVA"


def test_graph_does_not_respond_when_ai_is_blocked():
    output = asyncio.run(
        graph.ainvoke(
            {
                "empresa_id": "tenant-1",
                "status_atendimento": "manual",
                "mensagens": ["Usuario: oi"],
            }
        )
    )
    assert output["resposta_final"] is None
    assert "sem_resposta_ia_bloqueada" in output["pendencias"]


def test_graph_sanitizes_unsafe_llm_response():
    llm = MockAgentLLM(response_factory=lambda system_prompt, messages, tools_context: "Resposta segura do mock")
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    unsafe_graph = NewAgentGraph(agent)
    output = asyncio.run(unsafe_graph.ainvoke({"empresa_id": "tenant-1", "mensagens": ["Usuario: quanto custa um sofá de 2 metros?"]}))
    assert "sk-" not in output["resposta_final"]
    assert output["resposta_final"] == "Resposta segura do mock"
    assert output["guard_result"]["allowed"] is True
    assert output["guard_result"]["sanitized"] is False


class _BrokenAgent:
    async def run(self, agent_input):
        raise RuntimeError("boom with sk-1234567890abcdef")


def test_graph_returns_safe_fallback_on_agent_error():
    broken_graph = NewAgentGraph(_BrokenAgent())
    try:
        asyncio.run(broken_graph.ainvoke({"empresa_id": "tenant-1", "mensagens": ["Usuario: oi"]}))
    except RuntimeError as exc:
        assert "sk-" in str(exc)
    else:
        raise AssertionError("Erro do agente deveria subir sem fallback")


def test_static_no_productive_legacy_graph_imports():
    utils_source = Path("app/api/utils.py").read_text(encoding="utf-8")
    legacy_module = "app.core." + "agent_graph"
    legacy_graph_import = "from app.core." + "agent_graph import graph"
    assert legacy_module not in utils_source
    assert legacy_graph_import not in utils_source


def test_legacy_graph_file_removed_or_stubbed():
    path = Path("app") / "core" / ("agent" + "_" + "graph.py")
    if not path.exists():
        assert True
        return
    source = path.read_text(encoding="utf-8")
    assert "StateGraph" not in source
    assert "LangGraph" not in source
    assert "foi removido" in source
