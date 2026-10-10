from __future__ import annotations

from typing import Any

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result


DEFAULT_REQUIRED_FIELDS = (
    "nome",
    "endereco_ou_bairro",
    "melhor_dia",
    "melhor_turno",
    "telefone",
    "servico_desejado",
)


async def run_agenda_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
) -> AgentToolResult:
    slots = dict(state.slots or {})
    budget = dict(state.last_budget_estimate or {}) if isinstance(state.last_budget_estimate, dict) else {}
    orcamento_config = (tenant_config or {}).get("credenciais_canais", {}).get("orcamento") if isinstance((tenant_config or {}).get("credenciais_canais"), dict) else None
    required_fields = list(DEFAULT_REQUIRED_FIELDS)
    if isinstance(orcamento_config, dict):
        required_fields = [str(item) for item in orcamento_config.get("scheduling_required_fields") or required_fields if str(item or "").strip()]

    missing_fields = []
    mapping = {
        "nome": ("nome", "customer_name"),
        "endereco_ou_bairro": ("endereco", "bairro", "address", "endereco_bairro"),
        "melhor_dia": ("melhor_dia", "dia"),
        "melhor_turno": ("melhor_turno", "turno"),
        "telefone": ("telefone", "phone"),
        "servico_desejado": ("servico_desejado", "service_type"),
    }
    for field in required_fields:
        aliases = mapping.get(field, (field,))
        if any(str(slots.get(alias) or "").strip() for alias in aliases):
            continue
        if field == "servico_desejado" and (state.active_flow == "orcamento" or budget):
            continue
        missing_fields.append(field)

    data = {
        "last_budget_estimate": budget,
        "slots": slots,
        "required_fields": required_fields,
        "current_message": str((message.text if message else None) or state.mensagem_atual or "").strip(),
        "memory_update": {
            "active_flow": "agendamento",
            "active_intent": "agendamento",
            "pending_slot": "scheduling_details",
            "slots": slots,
            "last_budget_estimate": budget or None,
            "last_agent_question": "dados para agendamento",
            "scheduling_requested": True,
        },
        "actions": [
            {
                "action_type": "coletar_dados_agendamento",
                "scope": "agenda",
                "risk": "low",
                "requires_confirmation": False,
            }
        ],
        "summary": "Cliente pediu agendamento.",
    }
    if budget.get("amount") or budget.get("total"):
        data["summary"] = f"Cliente pediu agendamento após orçamento de {budget.get('amount') or budget.get('total')}."
    return make_tool_result(
        name="agenda",
        ok=True,
        data=data,
        missing_fields=missing_fields,
        error=None,
    )
