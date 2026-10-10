from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, model_validator


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class IncomingMessage:
    text: str
    tenant_id: str | None = None
    lead_id: str | None = None
    phone: str | None = None
    channel: str = "whatsapp"
    message_id: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    has_image: bool = False
    image_analysis: dict[str, Any] | None = None
    received_at: datetime = field(default_factory=_now_utc)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationState:
    empresa_id: str | None = None
    lead_id: str | None = None
    telefone: str | None = None
    canal: str = "whatsapp"
    nome: str | None = None
    historico_curto: list[str] = field(default_factory=list)
    resumo_cliente: str | None = None
    tags: list[str] = field(default_factory=list)
    etapa: str | None = None
    ia_bloqueada: bool = False
    status_atendimento: str = "ia_ativa"
    first_agent_response: bool = False
    mensagem_atual: str = ""
    has_image: bool = False
    media_type: str | None = None
    media_url: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    image_analysis: dict[str, Any] | None = None
    image_analysis_status: str = "not_requested"
    active_flow: str | None = None
    active_intent: str | None = None
    pending_slot: str | None = None
    last_agent_question: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    last_budget_estimate: dict[str, Any] | None = None
    handoff_requested: bool = False
    scheduling_requested: bool = False
    persisted_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IntentDecision:
    intent: str | None = None
    specialist_key: str | None = None
    confidence: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    should_respond: bool = True


@dataclass(slots=True)
class CommercialStateDecision:
    """Structured semantic decision; persistence is owned by the executor."""

    tags_to_apply: list[str] = field(default_factory=list)
    tags_to_remove: list[str] = field(default_factory=list)
    target_stage_id: str | None = None
    signals: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    operational_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tags_to_apply": list(self.tags_to_apply),
            "tags_to_remove": list(self.tags_to_remove),
            "target_stage_id": self.target_stage_id,
            "signals": list(self.signals),
            "reason_codes": list(self.reason_codes),
            "confidence": self.confidence,
            "operational_evidence": dict(self.operational_evidence),
        }


@dataclass(slots=True)
class ActionRequest:
    action_type: str
    scope: str = "conversation"
    risk: Literal["low", "medium", "high"] = "low"
    payload: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass(slots=True)
class ToolInvocation:
    tool_name: str
    tenant_id: str | None
    specialist_key: str
    tool_type: str = "native"
    scope: str = "conversation"
    risk: Literal["low", "medium", "high"] = "low"
    args: dict[str, Any] = field(default_factory=dict)
    url: str | None = None


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    allowed: bool
    success: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reason_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GuardResult:
    allowed: bool
    text: str
    reason_codes: list[str] = field(default_factory=list)
    sanitized: bool = False


@dataclass(slots=True)
class AtendimentoAgentInput:
    state: ConversationState
    messages: list[IncomingMessage] = field(default_factory=list)


class FirstTurnAgentDecision(BaseModel):
    needs_extension: bool = False
    extension: str = ""

    @model_validator(mode="after")
    def validate_extension_contract(self):
        self.extension = str(self.extension or "").strip()
        if self.needs_extension and not self.extension:
            raise ValueError("extension is required when needs_extension=true")
        if not self.needs_extension:
            self.extension = ""
        return self


@dataclass(slots=True)
class AtendimentoAgentOutput:
    response_text: str
    decision: IntentDecision
    guard_result: GuardResult | None = None
    should_dispatch: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    first_turn_decision: FirstTurnAgentDecision | None = None
