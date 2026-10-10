from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.schemas import AtendimentoAgentInput, ConversationState, IncomingMessage


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> None:
    tenant_config = {
        "empresa_id": "tenant-regras",
        "nome_empresa": "Tenant Regras",
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
                        "tiers": [{"max": 1.8, "price": 200}],
                    }
                },
            }
        },
    }
    llm = MockAgentLLM(responses=["A estimativa fica em R$ 250.", "A estimativa fica em R$ 200."])
    agent = AtendimentoAgent(llm=llm, tool_planner=MockToolPlanner(default_plan={
        "selected_tools": ["orcamento"],
        "tool_args": {},
        "expected_missing_fields": [],
        "intent_summary": "orcamento fixture",
    }))
    agent._load_tenant_config = AsyncMock(return_value=tenant_config)  # type: ignore[attr-defined]

    output = await agent.run(
        AtendimentoAgentInput(
            state=ConversationState(empresa_id="tenant-regras"),
            messages=[IncomingMessage(text="sofá 1,8 metros", tenant_id="tenant-regras")],
        )
    )

    assert_true("R$ 200" in output.response_text, "Resposta final não preservou fonte de verdade.")
    assert_true("R$ 250" not in output.response_text, "Resposta final manteve preço divergente.")
    assert_true(output.metadata.get("repair_attempted") is True, "Repair não foi acionado para divergência.")

    print("verificar_llm_nao_sobrescreve_regras: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"verificar_llm_nao_sobrescreve_regras: FALHOU: {exc}", file=sys.stderr)
        sys.exit(1)
