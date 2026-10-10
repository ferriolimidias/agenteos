from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.fiscal.response_fiscal import AgenteFiscal
from app.agents.guards.response_guard import ResponseGuard
from app.agents.llm.mock import MockAgentLLM
from app.agents.llm.openai_provider import OpenAIAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage
from app.agents.tools.registry import ToolRegistry


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _plan(*tools: str) -> dict:
    return {
        "selected_tools": list(tools),
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "fase3b_fixture",
    }


def _tenant_config() -> dict:
    return {
        "empresa_id": "tenant-fase3b",
        "nome_empresa": "Tenant Fase 3B",
        "nome_agente": "Assistente",
        "has_ai": True,
        "credenciais_canais": {
            "orcamento": {
                "enabled": True,
                "currency": "BRL",
                "services": {
                    "sofa": {
                        "label": "Sofá",
                        "aliases": ["sofá", "sofa"],
                        "pricing_type": "tier_by_measure",
                        "missing_field": "measure",
                        "tiers": [
                            {"max": 1.8, "price": 180},
                            {"max": 2.3, "price": 200},
                        ],
                    }
                },
            }
        },
    }


def _forbidden_tokens() -> tuple[str, ...]:
    return (
        "Intent" + "Router",
        "ROUTING" + "_RULES",
        "compose" + "_response",
        "safe_response" + "_hint",
        "Specialist" + "Registry",
        "Specialist" + "Result",
        "app.agents." + "specialists",
        "app.agents." + "routing",
    )


def check_removed_files() -> None:
    assert_true(not (ROOT / "app/agents/routing").exists(), "Diretorio antigo de roteamento ainda existe")
    assert_true(not (ROOT / "app/agents/specialists").exists(), "Diretorio antigo de especialistas ainda existe")
    assert_true(not (ROOT / "app/agents/atendimento/composer.py").exists(), "Composer antigo ainda existe")


def check_forbidden_imports() -> None:
    allowed_file = Path(__file__).resolve()
    python_files = list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))
    for path in python_files:
        if path.resolve() == allowed_file:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for token in _forbidden_tokens():
            assert_true(token not in source, f"Token antigo encontrado em {path.relative_to(ROOT)}: {token}")


async def check_runtime_new_flow() -> None:
    llm = MockAgentLLM(responses=["Resposta final LLM-first."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context")))
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-fase3b"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-fase3b")],
        )
    )
    assert_true(output.response_text == "Resposta final LLM-first.", "AtendimentoAgent nao produziu resposta LLM-first")
    assert_true(output.guard_result is not None and output.guard_result.allowed is True, "ResponseGuard bloqueou resposta segura")

    registry = ToolRegistry()
    plan = await MockToolPlanner(default_plan=_plan("orcamento")).plan(
        state=ConversationState(empresa_id="tenant-fase3b", mensagem_atual="sofá 1,8 metros"),
        tenant_config=_tenant_config(),
        memory={},
        history=[],
        available_tools=registry.available_tools(_tenant_config()),
    )
    assert_true(plan["selected_tools"] == ["orcamento"], f"Plano injetado foi alterado: {plan}")
    results = await registry.run_planned_tools(
        state=ConversationState(empresa_id="tenant-fase3b", mensagem_atual="sofá 1,8 metros"),
        plan=plan,
        message=IncomingMessage(text="sofá 1,8 metros", tenant_id="tenant-fase3b"),
        tenant_config=_tenant_config(),
    )
    assert_true([item["name"] for item in results] == ["orcamento"], f"Registry executou tools fora do plano: {results}")

    fiscal = AgenteFiscal()
    findings = fiscal.validate(
        response="Vou calcular e te retorno.",
        tool_results={"orcamento": {"estimate": {"amount": 180, "currency": "BRL"}, "missing_fields": []}},
        memory={},
        tenant_config=_tenant_config(),
    )
    assert_true(any(item["severity"] == "error" for item in findings), "AgenteFiscal nao reprovou resposta incoerente")

    guard = ResponseGuard().validate("segredo sk-1234567890abcdef")
    assert_true(guard.sanitized is True, "ResponseGuard nao sanitizou segredo")


async def check_gate_closed() -> None:
    llm = MockAgentLLM(responses=["nao usar"])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("context")))
    agent._load_tenant_config = AsyncMock(return_value={"empresa_id": "tenant-sem-ia", "has_ai": False})  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-sem-ia"),
            messages=[IncomingMessage(text="oi", tenant_id="tenant-sem-ia")],
        )
    )
    assert_true(output.response_text == "", "Tenant sem IA recebeu resposta")
    assert_true(output.decision.should_respond is False, "Tenant sem IA nao falhou fechado")
    assert_true(not llm.calls, "LLM foi chamada com IA desabilitada")


async def check_budget_repair() -> None:
    llm = MockAgentLLM(responses=["Vou calcular e te retorno.", "O orçamento prévio fica em R$ 180."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan=_plan("orcamento")))
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-fase3b"),
            messages=[IncomingMessage(text="sofá 1,8 metros", tenant_id="tenant-fase3b")],
        )
    )
    assert_true("R$ 180" in output.response_text, f"Repair nao preservou orçamento: {output.response_text}")
    assert_true(output.metadata.get("repair_attempted") is True, "Repair loop nao foi acionado")
    tool_results = output.metadata.get("tool_results") or []
    assert_true(tool_results and tool_results[0]["data"]["estimate"]["amount"] == 180, "Tool de orçamento nao retornou dados estruturados")


async def check_no_real_openai() -> None:
    async def fail_openai(*args, **kwargs):
        raise AssertionError("OpenAI real foi chamado")

    llm = MockAgentLLM(responses=["Resposta mock."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner())
    agent._load_tenant_config = AsyncMock(return_value=_tenant_config())  # type: ignore[attr-defined]
    with patch.object(OpenAIAgentLLM, "generate", new=fail_openai):
        output = await agent.run(
            AtendimentoAgentInput(
                state=ConversationState(empresa_id="tenant-fase3b"),
                messages=[IncomingMessage(text="oi", tenant_id="tenant-fase3b")],
            )
        )
    assert_true(output.response_text == "Resposta mock.", "Fluxo mock falhou")


async def main() -> None:
    check_removed_files()
    check_forbidden_imports()
    await check_runtime_new_flow()
    await check_gate_closed()
    await check_budget_repair()
    await check_no_real_openai()
    print("verificar_fase3b_remocao_codigo_antigo_agentes: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"verificar_fase3b_remocao_codigo_antigo_agentes: FALHOU: {exc}", file=sys.stderr)
        sys.exit(1)
