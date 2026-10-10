from __future__ import annotations

from typing import Any, Mapping

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.state.memory import apply_memory_to_state, normalize_memory


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _first_media(data: Mapping[str, Any], incoming: IncomingMessage | None) -> dict[str, Any]:
    media_messages = data.get("media_messages")
    first = media_messages[0] if isinstance(media_messages, list) and media_messages else {}
    first = first if isinstance(first, dict) else {}
    metadata = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
    incoming_meta = incoming.metadata if incoming else {}
    return {
        "media_type": (
            getattr(incoming, "media_type", None)
            or data.get("media_type")
            or first.get("media_type")
            or first.get("tipo_mensagem")
            or metadata.get("media_type")
        ),
        "media_url": getattr(incoming, "media_url", None) or data.get("media_url") or first.get("media_url"),
        "media_id": (
            getattr(incoming, "media_id", None)
            or data.get("media_id")
            or first.get("media_id")
            or metadata.get("media_id")
        ),
        "media_mime_type": (
            getattr(incoming, "media_mime_type", None)
            or data.get("media_mime_type")
            or first.get("media_mime_type")
            or metadata.get("media_mime_type")
        ),
        "caption": getattr(incoming, "caption", None) or data.get("caption") or first.get("caption") or metadata.get("caption"),
        "has_image": bool(
            getattr(incoming, "has_image", False)
            or data.get("has_image")
            or incoming_meta.get("has_image")
            or first.get("has_image")
            or first.get("tipo_mensagem") == "image"
            or first.get("media_type") == "image"
            or metadata.get("media_type") == "image"
        ),
        "image_analysis": (
            getattr(incoming, "image_analysis", None)
            or data.get("image_analysis")
            or incoming_meta.get("image_analysis")
        ),
        "image_analysis_status": (
            data.get("image_analysis_status")
            or incoming_meta.get("image_analysis_status")
            or ("available" if (getattr(incoming, "image_analysis", None) or data.get("image_analysis")) else "not_requested")
        ),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _conversation_memory(data: Mapping[str, Any], incoming: IncomingMessage | None) -> dict[str, Any]:
    metadata = incoming.metadata if incoming else {}
    candidates = (
        data.get("conversation_state"),
        data.get("estado_conversacional"),
        data.get("operational_state"),
        metadata.get("conversation_state") if isinstance(metadata, Mapping) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return dict(candidate)
    return {}


def build_conversation_state(
    source: Mapping[str, Any] | None = None,
    incoming: IncomingMessage | None = None,
) -> ConversationState:
    data = dict(source or {})
    current_text = (
        str(incoming.text).strip()
        if incoming and str(incoming.text or "").strip()
        else str(data.get("mensagem_atual") or data.get("ultima_mensagem") or "").strip()
    )
    historico_raw = data.get("historico_curto") or data.get("mensagens") or []
    historico = _as_list(historico_raw)
    status_atendimento = str(data.get("status_atendimento") or data.get("status_conversa") or "ia_ativa").strip() or "ia_ativa"
    ia_bloqueada = bool(data.get("ia_bloqueada") or data.get("ai_blocked") or False)
    if status_atendimento.lower() in {"manual", "aguardando_humano", "handoff"}:
        ia_bloqueada = True
    media = _first_media(data, incoming)
    memory = normalize_memory(
        {
            **_conversation_memory(data, incoming),
            **{
                key: data.get(key)
                for key in (
                    "active_flow",
                    "active_intent",
                    "pending_slot",
                    "last_agent_question",
                    "last_budget_estimate",
                    "handoff_requested",
                    "scheduling_requested",
                )
                if data.get(key) not in (None, "", {}, [])
            },
        }
    )
    slots = {
        **_as_dict(memory.get("slots")),
        **_as_dict(data.get("slots")),
    }

    state = ConversationState(
        empresa_id=str(data.get("empresa_id") or (incoming.tenant_id if incoming else "") or "").strip() or None,
        lead_id=str(data.get("lead_id") or (incoming.lead_id if incoming else "") or "").strip() or None,
        telefone=str(data.get("telefone") or data.get("identificador_origem") or (incoming.phone if incoming else "") or "").strip() or None,
        canal=str(data.get("canal") or (incoming.channel if incoming else "whatsapp") or "whatsapp").strip(),
        nome=str(data.get("nome") or data.get("nome_contato") or "").strip() or None,
        historico_curto=historico[-10:],
        resumo_cliente=str(data.get("resumo_cliente") or "").strip() or None,
        tags=_as_list(data.get("tags")),
        etapa=str(data.get("etapa") or data.get("etapa_funil") or "").strip() or None,
        ia_bloqueada=ia_bloqueada,
        status_atendimento=status_atendimento,
        first_agent_response=bool(data.get("first_agent_response") or data.get("primeiro_contato") or False),
        mensagem_atual=current_text,
        has_image=bool(media["has_image"]),
        media_type=str(media["media_type"] or "").strip() or None,
        media_url=str(media["media_url"] or "").strip() or None,
        media_id=str(media["media_id"] or "").strip() or None,
        media_mime_type=str(media["media_mime_type"] or "").strip() or None,
        caption=str(media["caption"] or "").strip() or None,
        image_analysis=media["image_analysis"] if isinstance(media["image_analysis"], dict) else None,
        image_analysis_status=str(media["image_analysis_status"] or "not_requested").strip() or "not_requested",
        active_flow=str(data.get("active_flow") or memory.get("active_flow") or "").strip() or None,
        active_intent=str(data.get("active_intent") or memory.get("active_intent") or "").strip() or None,
        pending_slot=str(data.get("pending_slot") or memory.get("pending_slot") or "").strip() or None,
        last_agent_question=str(data.get("last_agent_question") or memory.get("last_agent_question") or "").strip() or None,
        slots=slots,
        last_budget_estimate=(
            _as_dict(data.get("last_budget_estimate"))
            or _as_dict(memory.get("last_budget_estimate"))
            or None
        ),
        handoff_requested=bool(data.get("handoff_requested") or memory.get("handoff_requested") or False),
        scheduling_requested=bool(data.get("scheduling_requested") or memory.get("scheduling_requested") or False),
        persisted_state=memory,
        metadata={
            **{k: v for k, v in data.items() if k not in {"openai_api_key", "evolution_apikey", "api_key"}},
            **(incoming.metadata if incoming else {}),
            "conversation_state": memory,
            "has_image": bool(media["has_image"]),
            "media_type": str(media["media_type"] or "").strip() or None,
            "media_id": str(media["media_id"] or "").strip() or None,
            "media_mime_type": str(media["media_mime_type"] or "").strip() or None,
            "caption": str(media["caption"] or "").strip() or None,
            "image_analysis": media["image_analysis"] if isinstance(media["image_analysis"], dict) else None,
            "image_analysis_status": str(media["image_analysis_status"] or "not_requested").strip() or "not_requested",
        },
    )
    return apply_memory_to_state(state, normalize_memory({**memory, "slots": slots}, state))
