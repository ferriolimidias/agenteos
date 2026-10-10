import asyncio
from unittest.mock import AsyncMock

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage


def test_price_message_returns_safe_final_response():
    agent = AtendimentoAgent(llm=MockAgentLLM(responses=["Posso ajudar com valores."]), tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    state = ConversationState(empresa_id="tenant-1")
    output = asyncio.run(agent.run(AtendimentoAgentInput(state=state, messages=[IncomingMessage(text="quanto custa?")])))
    assert output.guard_result.allowed is True
    assert output.response_text
    assert output.should_dispatch is False


def test_human_request_returns_safe_final_response():
    agent = AtendimentoAgent(llm=MockAgentLLM(responses=["Vou encaminhar para a equipe."]), tool_planner=MockToolPlanner(default_plan={
        "selected_tools": ["handoff"],
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "handoff fixture",
    }))
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    state = ConversationState(empresa_id="tenant-1")
    output = asyncio.run(agent.run(AtendimentoAgentInput(state=state, messages=[IncomingMessage(text="quero falar com atendente")])))
    assert output.guard_result.allowed is True
    assert output.should_dispatch is False


def test_tools_do_not_send_direct_response():
    agent = AtendimentoAgent(llm=MockAgentLLM(responses=["Resposta final da LLM."]), tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    output = asyncio.run(agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-1"),
            messages=[IncomingMessage(text="quanto custa?")],
        )
    ))
    assert output.response_text == "Resposta final da LLM."


def test_response_passes_through_guard():
    agent = AtendimentoAgent(llm=MockAgentLLM(responses=["Entendi o erro relatado."]), tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    output = asyncio.run(agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-1"),
            messages=[IncomingMessage(text="meu bot deu erro")],
        )
    ))
    assert output.guard_result is not None
    assert output.guard_result.allowed is True


def test_manual_status_does_not_generate_customer_response():
    agent = AtendimentoAgent(llm=MockAgentLLM())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": True})  # type: ignore[attr-defined]
    output = asyncio.run(agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-1", ia_bloqueada=True, status_atendimento="manual"),
            messages=[IncomingMessage(text="oi")],
        )
    ))
    assert output.decision.should_respond is False
    assert output.response_text == ""
    assert output.guard_result.allowed is True


def test_ai_disabled_does_not_generate_customer_response():
    llm = MockAgentLLM(responses=["nao usar"])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-1", "has_ai": False})  # type: ignore[attr-defined]
    output = asyncio.run(agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-1"),
            messages=[IncomingMessage(text="oi")],
        )
    ))
    assert output.decision.should_respond is False
    assert output.response_text == ""
    assert not llm.calls
