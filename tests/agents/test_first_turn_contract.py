import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.atendimento.first_turn import compose_first_turn_response
from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage


GREETING = "Olá! Base configurada. Encontro sábado às 15h. Saiba mais: https://example.test/a"


def _run(first: bool, inbound: str = "Oi", response: str = "Resposta do agente."):
    llm = MockAgentLLM(responses=[response])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value={
        "empresa_id": "tenant-a",
        "has_ai": True,
        "nome_agente": "Agente A",
        "mensagem_saudacao": GREETING,
        "ia_identidade": "Tom cordial.",
        "ia_regras_negocio": "FACT_ALPHA está disponível.",
    })  # type: ignore[attr-defined]
    output = asyncio.run(agent.run(AtendimentoAgentInput(
        state=ConversationState(empresa_id="tenant-a", first_agent_response=first),
        messages=[IncomingMessage(text=inbound)],
    )))
    return output, llm


def test_first_turn_question_already_covered_uses_agent_with_priority_context():
    output, llm = _run(True, "Quero saber mais sobre o encontro.", json.dumps({"needs_extension": False, "extension": ""}))
    prompt = llm.calls[0]["system_prompt"]
    assert output.response_text == ""
    assert output.first_turn_decision is not None
    assert output.first_turn_decision.needs_extension is False
    assert "Esta é a primeira resposta da IA neste atendimento." in prompt
    assert "<CONFIGURED_GREETING>" in prompt
    assert "</CONFIGURED_GREETING>" in prompt
    assert GREETING in prompt
    assert "Copie integralmente e literalmente" in prompt


def test_first_turn_extra_grounded_question_keeps_agent_active():
    output, llm = _run(True, "Olá, quanto custa?", json.dumps({"needs_extension": True, "extension": "Custa PRICE_ALPHA."}))
    assert output.response_text == "Custa PRICE_ALPHA."
    assert llm.calls
    assert "complemento curto, grounded e necessário" in llm.calls[0]["system_prompt"]


def test_first_turn_unknown_extra_question_preserves_no_invention_rule():
    output, llm = _run(True, "Tem estacionamento?", json.dumps({"needs_extension": False, "extension": ""}))
    assert output.response_text == ""
    prompt = llm.calls[0]["system_prompt"]
    assert "não invente fatos" in prompt


def test_first_turn_simple_greeting_is_agent_generated_from_configured_base():
    output, llm = _run(True, "Oi", json.dumps({"needs_extension": False, "extension": ""}))
    assert output.response_text == ""
    assert "<CONFIGURED_GREETING>" in llm.calls[0]["system_prompt"]


def test_first_turn_covered_question_exact_greeting():
    output, _ = _run(True, "Pode me passar mais informações?", json.dumps({"needs_extension": False, "extension": ""}))
    assert compose_first_turn_response(GREETING, output.first_turn_decision) == GREETING


def test_first_turn_simple_message_exact_greeting():
    output, _ = _run(True, "Oi", json.dumps({"needs_extension": False, "extension": ""}))
    assert compose_first_turn_response(GREETING, output.first_turn_decision) == GREETING


def test_first_turn_greeting_plus_extra_question_has_exact_prefix():
    output, _ = _run(True, "Oi, e quanto custa?", json.dumps({"needs_extension": True, "extension": "Custa PRICE_ALPHA."}))
    final = compose_first_turn_response(GREETING, output.first_turn_decision)
    assert final.startswith(GREETING)


def test_first_turn_no_redundant_rewrite_and_cta_formatting_preserved():
    output, _ = _run(True, "Pode me passar mais informações?", json.dumps({"needs_extension": False, "extension": ""}))
    final = compose_first_turn_response(GREETING, output.first_turn_decision)
    assert final == GREETING
    assert "https://example.test/a" in final


def test_second_turn_is_normal_agent_behavior_without_first_turn_contract():
    output, llm = _run(False, "Pode explicar?")
    assert output.response_text == "Resposta do agente."
    assert "Esta é a primeira resposta da IA neste atendimento." not in llm.calls[0]["system_prompt"]


def test_global_greeting_exact_equality_is_not_required():
    source = Path("app/agents/atendimento/prompt_builder.py").read_text()
    assert "PRIORITY_BASE_WITH_MINIMAL_GROUNDED_EXTENSION" not in source
    assert "CONFIGURED_GREETING" in source


def test_first_turn_exact_prefix_and_formatting_contract():
    _, llm = _run(True, "Oi, e quanto custa?")
    prompt = llm.calls[0]["system_prompt"]
    assert 'needs_extension' in prompt
    assert "não o copie" in prompt
    block = prompt.rsplit("<CONFIGURED_GREETING>", 1)[1].split("</CONFIGURED_GREETING>", 1)[0].strip()
    assert block == GREETING


def test_first_turn_persona_rules_context_and_tools_cannot_rewrite_greeting():
    _, llm = _run(True, "Quero mais informações")
    prompt = llm.calls[0]["system_prompt"]
    assert "não o copie e não o parafraseie" in prompt


def test_second_turn_greeting_not_forced():
    _, llm = _run(False, "Pode explicar?")
    assert "<CONFIGURED_GREETING>" not in llm.calls[0]["system_prompt"]
