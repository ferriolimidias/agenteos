"""Tool tenant-scoped para a IA consultar e aplicar tags explicitamente permitidas."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result
from app.services.commercial_state_executor import CommercialStateDecision, execute_commercial_state_for_lead
from db.database import AsyncSessionLocal
from db.models import TagCRM


async def _allowed_ids(empresa_id: str | None) -> set[str]:
    try:
        tenant_id = uuid.UUID(str(empresa_id))
    except (TypeError, ValueError):
        return set()
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(TagCRM.id).where(TagCRM.empresa_id == tenant_id, TagCRM.ai_can_apply.is_(True))
        )
        return {str(tag_id) for tag_id in rows.scalars().all()}


async def run_tags_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
    tool_args: dict[str, Any] | None = None,
) -> AgentToolResult:
    allowed = await _allowed_ids(state.empresa_id)
    status = str(getattr(state, "status_atendimento", "") or "").strip().lower()
    if status in {"manual", "aguardando_humano", "handoff"} or bool(getattr(state, "ia_bloqueada", False)):
        return make_tool_result(name="tags", ok=False, error="tags_not_available")
    if not allowed or not state.empresa_id or not state.lead_id:
        return make_tool_result(name="tags", ok=False, error="tags_not_available")

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(TagCRM).where(
                TagCRM.empresa_id == uuid.UUID(str(state.empresa_id)),
                TagCRM.id.in_([uuid.UUID(item) for item in allowed]),
            )
        )
        tags = rows.scalars().all()
    listed = [{"id": str(tag.id), "name": tag.nome, "description": tag.instrucao_ia or ""} for tag in tags]
    requested = str((tool_args or {}).get("tag_id") or "").strip()
    if not requested:
        return make_tool_result(name="tags", ok=True, data={"allowed_tags": listed})
    if requested not in allowed:
        return make_tool_result(name="tags", ok=False, data={"allowed_tags": listed}, error="tag_not_allowed")
    selected_tag = next((tag for tag in tags if str(tag.id) == requested), None)
    if selected_tag is not None and bool(getattr(selected_tag, "acao_transferir_humano", False)):
        return make_tool_result(
            name="tags",
            ok=False,
            data={"allowed_tags": listed},
            error="handoff_requires_handoff_tool",
        )
    result = await execute_commercial_state_for_lead(
        empresa_id=str(state.empresa_id),
        lead_id=str(state.lead_id),
        decision=CommercialStateDecision(tags_to_apply=[requested]),
        source="ai",
        source_event_id="commercial_state_tool",
    )
    return make_tool_result(
        name="tags",
        ok=result.get("status") in {"applied", "no_change"},
        data={"allowed_tags": listed, "applied_tag_id": requested},
        actions=[{"type": "apply_tag", "tag_id": requested}],
        error=None if result.get("status") in {"applied", "no_change"} else str(result.get("reason") or "tag_apply_failed"),
    )
