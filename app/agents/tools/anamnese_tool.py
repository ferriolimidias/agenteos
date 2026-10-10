from __future__ import annotations

import logging
from typing import Any

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result
from app.core.secret_sanitizer import sanitize_for_log
from app.services.anamnese_service import AnamneseServiceError, criar_ou_obter_anamnese


logger = logging.getLogger(__name__)


async def run_anamnese_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
) -> AgentToolResult:
    empresa_id = str(state.empresa_id or "").strip()
    lead_id = str(state.lead_id or "").strip()
    if not empresa_id or not lead_id:
        return make_tool_result(
            name="anamnese",
            ok=False,
            missing_fields=[field for field, value in (("empresa_id", empresa_id), ("lead_id", lead_id)) if not value],
            error="missing_required_context",
        )

    metadata = dict(state.metadata or {})
    if message and isinstance(message.metadata, dict):
        metadata.update(message.metadata)
    conversation_id = str(
        metadata.get("conversation_id")
        or metadata.get("channel_conversation_id")
        or ""
    ).strip() or None

    try:
        anamnese = await criar_ou_obter_anamnese(
            empresa_id=empresa_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
        )
    except AnamneseServiceError as exc:
        logger.warning(
            "ai.tool.failed tool=anamnese empresa_id=%s lead_id=%s error_class=%s reason=%s",
            sanitize_for_log(empresa_id),
            sanitize_for_log(lead_id),
            exc.__class__.__name__,
            sanitize_for_log(str(exc))[:160],
        )
        return make_tool_result(name="anamnese", ok=False, error="anamnese_service_error")
    except Exception as exc:
        logger.exception(
            "ai.tool.failed tool=anamnese empresa_id=%s lead_id=%s error_class=%s",
            sanitize_for_log(empresa_id),
            sanitize_for_log(lead_id),
            exc.__class__.__name__,
        )
        return make_tool_result(name="anamnese", ok=False, error="anamnese_unexpected_error")

    logger.warning(
        "ai.tool.finished tool=anamnese empresa_id=%s lead_id=%s anamnese_id=%s outcome=%s",
        sanitize_for_log(empresa_id),
        sanitize_for_log(lead_id),
        sanitize_for_log(anamnese.anamnese_id),
        "created" if anamnese.created else "reused",
    )
    return make_tool_result(
        name="anamnese",
        ok=True,
        data={
            "success": True,
            "created": anamnese.created,
            "reused": anamnese.reused,
            "anamnese_id": anamnese.anamnese_id,
            "url": anamnese.url,
            "response_instruction": (
                "Confirme de forma breve e natural que a avaliacao esta pronta, "
                "inclua exatamente a URL fornecida e indique que o proximo passo aparece ao concluir."
            ),
            "summary": "Anamnese criada ou recuperada com link valido.",
        },
        memory_update={
            "active_flow": "anamnese",
            "active_intent": "anamnese_link_sent",
        },
    )
