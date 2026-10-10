from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.agents.schemas import ConversationState


MEMORY_FIELDS = {
    "empresa_id",
    "lead_id",
    "channel",
    "identificador",
    "active_flow",
    "active_intent",
    "pending_slot",
    "slots",
    "last_budget_estimate",
    "last_agent_question",
    "last_user_message",
    "handoff_requested",
    "scheduling_requested",
    "updated_at",
    "first_agent_response",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_memory(memory: Mapping[str, Any] | None, state: ConversationState | None = None) -> dict[str, Any]:
    source = dict(memory or {})
    slots = source.get("slots") if isinstance(source.get("slots"), Mapping) else {}
    payload: dict[str, Any] = {
        "empresa_id": source.get("empresa_id") or (state.empresa_id if state else None),
        "lead_id": source.get("lead_id") or (state.lead_id if state else None),
        "channel": source.get("channel") or source.get("canal") or (state.canal if state else None),
        "identificador": source.get("identificador") or source.get("telefone") or (state.telefone if state else None),
        "active_flow": source.get("active_flow"),
        "active_intent": source.get("active_intent"),
        "pending_slot": source.get("pending_slot"),
        "slots": dict(slots),
        "last_budget_estimate": source.get("last_budget_estimate") if isinstance(source.get("last_budget_estimate"), Mapping) else None,
        "last_agent_question": source.get("last_agent_question"),
        "last_user_message": source.get("last_user_message"),
        "handoff_requested": bool(source.get("handoff_requested") or False),
        "scheduling_requested": bool(source.get("scheduling_requested") or False),
        "updated_at": source.get("updated_at") or utc_now_iso(),
        "first_agent_response": bool(source.get("first_agent_response") or False),
    }
    return {key: value for key, value in payload.items() if key in MEMORY_FIELDS and value not in (None, "", {}, [])}


def memory_from_state(state: ConversationState) -> dict[str, Any]:
    return normalize_memory(
        {
            "empresa_id": state.empresa_id,
            "lead_id": state.lead_id,
            "channel": state.canal,
            "identificador": state.telefone,
            "active_flow": state.active_flow,
            "active_intent": state.active_intent,
            "pending_slot": state.pending_slot,
            "slots": dict(state.slots or {}),
            "last_budget_estimate": state.last_budget_estimate,
            "last_agent_question": state.last_agent_question,
            "last_user_message": state.persisted_state.get("last_user_message") if isinstance(state.persisted_state, Mapping) else None,
            "handoff_requested": state.handoff_requested,
            "scheduling_requested": state.scheduling_requested,
            "first_agent_response": state.first_agent_response,
        },
        state,
    )


def apply_memory_to_state(state: ConversationState, memory: Mapping[str, Any] | None) -> ConversationState:
    normalized = normalize_memory(memory, state)
    state.active_flow = normalized.get("active_flow")
    state.active_intent = normalized.get("active_intent")
    state.pending_slot = normalized.get("pending_slot")
    state.last_agent_question = normalized.get("last_agent_question")
    state.slots = dict(normalized.get("slots") or {})
    state.last_budget_estimate = (
        dict(normalized.get("last_budget_estimate"))
        if isinstance(normalized.get("last_budget_estimate"), Mapping)
        else None
    )
    state.handoff_requested = bool(normalized.get("handoff_requested") or False)
    state.scheduling_requested = bool(normalized.get("scheduling_requested") or False)
    state.persisted_state = dict(normalized)
    state.metadata["conversation_state"] = dict(normalized)
    return state


def merge_memory_update(
    current_memory: Mapping[str, Any] | None,
    specialist_result: Any | None,
    current_message: str | None = None,
    state: ConversationState | None = None,
) -> dict[str, Any]:
    merged = normalize_memory(current_memory, state)
    patch = {}
    if specialist_result and isinstance(specialist_result.data, Mapping):
        raw_patch = specialist_result.data.get("memory_update")
        patch = dict(raw_patch) if isinstance(raw_patch, Mapping) else {}
    if not patch:
        merged["updated_at"] = utc_now_iso()
        return merged

    if isinstance(patch.get("slots"), Mapping):
        merged["slots"] = {**dict(merged.get("slots") or {}), **dict(patch.get("slots") or {})}

    for key in (
        "active_flow",
        "active_intent",
        "pending_slot",
        "last_agent_question",
        "last_budget_estimate",
        "handoff_requested",
        "scheduling_requested",
    ):
        if key in patch:
            merged[key] = patch[key]

    if state:
        merged["empresa_id"] = state.empresa_id
        merged["lead_id"] = state.lead_id
        merged["channel"] = state.canal
        merged["identificador"] = state.telefone
    if current_message:
        merged["last_user_message"] = str(current_message).strip()
    merged["updated_at"] = utc_now_iso()
    return normalize_memory(merged, state)
