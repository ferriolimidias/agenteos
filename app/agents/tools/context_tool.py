from __future__ import annotations

from typing import Any

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result


async def run_context_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
) -> AgentToolResult:
    history = [str(item or "").strip() for item in (state.historico_curto or []) if str(item or "").strip()]
    context = {
        "current_message": str((message.text if message else None) or state.mensagem_atual or "").strip(),
        "history_excerpt": history[-5:],
        "memory": {
            "empresa_id": state.empresa_id,
            "lead_id": state.lead_id,
            "active_flow": state.active_flow,
            "active_intent": state.active_intent,
            "pending_slot": state.pending_slot,
            "last_agent_question": state.last_agent_question,
            "slots": dict(state.slots or {}),
            "last_budget_estimate": dict(state.last_budget_estimate or {}) if isinstance(state.last_budget_estimate, dict) else {},
            "handoff_requested": bool(state.handoff_requested),
            "scheduling_requested": bool(state.scheduling_requested),
        },
        "tenant_name": str((tenant_config or {}).get("nome_empresa") or "").strip(),
        "agent_name": str((tenant_config or {}).get("nome_agente") or "").strip(),
    }
    return make_tool_result(name="context", ok=True, data=context, missing_fields=[], error=None)

