from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result


async def run_handoff_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
) -> AgentToolResult:
    human_tag_id = None
    if state.empresa_id and state.lead_id:
        # The tag is resolved by its operational action, never by a tenant or a
        # display name.  Applying it uses the existing CRM service, which pauses
        # the bot and preserves the normal transfer workflow.
        from db.database import AsyncSessionLocal
        from db.models import TagCRM
        from app.services.commercial_state_executor import CommercialStateDecision, execute_commercial_state_for_lead

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == state.empresa_id,
                    TagCRM.acao_transferir_humano.is_(True),
                )
            )
            tag = result.scalars().first()
        if tag is not None:
            human_tag_id = str(tag.id)
            await execute_commercial_state_for_lead(
                lead_id=str(state.lead_id),
                empresa_id=str(state.empresa_id),
                decision=CommercialStateDecision(tags_to_apply=[human_tag_id]),
                source="system",
                source_event_id="handoff",
            )
    slots = dict(state.slots or {})
    budget = dict(state.last_budget_estimate or {}) if isinstance(state.last_budget_estimate, dict) else {}
    context_summary_parts = []
    if slots.get("service_type"):
        context_summary_parts.append(f"servico={slots.get('service_type')}")
    if budget.get("amount") or budget.get("total"):
        context_summary_parts.append(f"orcamento={budget.get('amount') or budget.get('total')}")
    if state.pending_slot:
        context_summary_parts.append(f"pendente={state.pending_slot}")
    if state.active_flow:
        context_summary_parts.append(f"fluxo={state.active_flow}")
    context_summary = ", ".join(context_summary_parts)

    data = {
        "reason": "human_request",
        "context_summary": context_summary,
        "slots": slots,
        "last_budget_estimate": budget,
        "current_message": str((message.text if message else None) or state.mensagem_atual or "").strip(),
        "memory_update": {
            "active_flow": state.active_flow,
            "active_intent": state.active_intent,
            "pending_slot": state.pending_slot,
            "slots": slots,
            "last_budget_estimate": budget or None,
            "handoff_requested": True,
        },
        "actions": [
            {
                "action_type": "solicitar_transferencia_humana",
                "scope": "handoff",
                "risk": "medium",
                "requires_confirmation": False,
            }
        ],
        "human_tag_id": human_tag_id,
    }
    return make_tool_result(name="handoff", ok=True, data=data, missing_fields=[], error=None)
