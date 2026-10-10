import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import LLMToolPlanner
from app.agents.schemas import ConversationState


def test_llm_tool_planner_includes_tag_context_and_conversation_state():
    llm = MockAgentLLM(
        responses=[
            json.dumps(
                {
                    "selected_tools": ["context", "tags"],
                    "tool_args": {"tags": {"tag_id": "tag-1"}},
                    "expected_missing_fields": [],
                    "intent_summary": "tag flow",
                    "commercial_state": {},
                },
                ensure_ascii=False,
            )
        ]
    )
    planner = LLMToolPlanner(llm=llm)
    state = ConversationState(
        empresa_id="tenant-1",
        lead_id="lead-1",
        status_atendimento="ia_ativa",
        tags=["tag-a"],
        active_flow="dc",
        active_intent="orcamento",
        pending_slot="data",
        handoff_requested=False,
        scheduling_requested=True,
    )
    tenant_config = {
        "empresa_id": "tenant-1",
        "nome_empresa": "Tenant de teste",
        "has_ai": True,
    }

    with patch(
        "app.agents.planner.tool_planner._load_commercial_context",
        new=AsyncMock(
            return_value=([
                {
                    "id": "tag-1",
                    "nome": "DC - Pediu informações",
                    "instrucao_ia": "Aplicar quando houver interesse contextual.",
                    "ai_can_apply": True,
                    "ativa_no_funil": False,
                    "disparar_conversao_ads": True,
                    "acao_transferir_humano": False,
                    "pausa_permanente": False,
                }
            ], []),
        ),
    ) as load_tag_context:
        plan = asyncio.run(
            planner.plan(
                state=state,
                tenant_config=tenant_config,
                memory={"lead_id": "lead-1"},
                history=[{"role": "user", "content": "oi"}],
                available_tools=["context", "tags"],
            )
        )

    assert plan["selected_tools"] == ["context", "tags"]
    assert llm.calls, "o planner deve chamar o modelo interno"
    payload = json.loads(llm.calls[0]["messages"][0]["content"])
    assert payload["estado_conversa"]["tags_aplicadas"] == ["tag-a"]
    assert payload["estado_conversa"]["status_atendimento"] == "ia_ativa"
    assert payload["tags_permitidas_ia"][0]["nome"] == "DC - Pediu informações"
    assert "etapas_disponiveis" in payload
    assert "ai_can_apply=true" in llm.calls[0]["system_prompt"]
    load_tag_context.assert_awaited_once_with(tenant_config)
