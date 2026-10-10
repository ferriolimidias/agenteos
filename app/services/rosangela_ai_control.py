from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from db.models import CRMLead, ChannelConversation, Conexao, Empresa
from app.services.whatsapp_address import (
    compare_whatsapp_addresses,
    mask_whatsapp_address,
    normalize_whatsapp_address,
)


CUSTOMER_INBOUND_TRIGGER = "customer_inbound_message"


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_phone(value: str | None, *, default_country_code: str | None = None) -> str:
    default_country = "BR" if re.sub(r"\D", "", str(default_country_code or "")) == "55" else None
    if default_country is None and not default_country_code:
        return re.sub(r"\D", "", str(value or ""))
    return normalize_whatsapp_address(value, default_country=default_country).digits


def _infer_country_code_from_connection(conexao: Conexao | None) -> str | None:
    if conexao is None:
        return None
    metadata = getattr(conexao, "metadata_json", None) or {}
    raw = (
        getattr(conexao, "display_number", None)
        or getattr(conexao, "display_phone_number", None)
        or metadata.get("display_phone_number")
    )
    digits = normalize_phone(str(raw or ""))
    if digits.startswith("55"):
        return "55"
    return None


def phones_match(left: str | None, right: str | None, *, default_country_code: str | None = None) -> bool:
    default_country = "BR" if re.sub(r"\D", "", str(default_country_code or "")) == "55" else None
    return compare_whatsapp_addresses(left, right, default_country=default_country).match


def mask_phone(value: str | None, *, default_country_code: str | None = None) -> str | None:
    default_country = "BR" if re.sub(r"\D", "", str(default_country_code or "")) == "55" else None
    return mask_whatsapp_address(value, default_country=default_country)


def parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class ConversationAIState:
    state: str
    reason: str
    reason_label: str | None = None
    manual_ai_enabled: bool | None = None
    phone_match_state: str | None = None
    human_state: str | None = None
    tenant_ai_state: str | None = None
    connection_state: str | None = None
    provider_state: str | None = None
    connection_id: str | None = None
    provider: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "reason_code": self.reason,
            "reason_label": self.reason_label or reason_label(self.reason),
            "manual_ai_enabled": self.manual_ai_enabled,
            "phone_match_state": self.phone_match_state,
            "human_state": self.human_state,
            "tenant_ai_state": self.tenant_ai_state,
            "connection_state": self.connection_state,
            "provider_state": self.provider_state,
            "connection_id": self.connection_id,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class ConversationManualAIState:
    manual_enabled: bool
    source: str
    updated_at: datetime | None = None
    actor: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "manual_enabled": self.manual_enabled,
            "source": self.source,
            "updated_at": iso(self.updated_at),
            "actor": self.actor,
            "reason": self.reason,
        }


REASON_LABELS = {
    "allowed": "IA autorizada",
    "ai_manual_disabled": "IA desligada nesta conversa",
    "human_takeover": "Atendimento humano ativo",
    "conversation_closed": "Conversa concluida",
    "tenant_ai_disabled": "IA do tenant desativada",
    "prompt_missing": "Prompt ausente",
    "model_missing": "Modelo ausente",
    "credentials_missing": "Credenciais ausentes",
    "connection_mismatch": "Conexao diferente da autorizada",
    "provider_mismatch": "Provider diferente do autorizado",
    "connection_unavailable": "Conexao indisponivel",
    "duplicate_inbound": "Inbound duplicado",
    "already_processed": "Inbound ja processado",
    "internal_error": "Erro interno",
}


def reason_label(reason: str | None) -> str:
    return REASON_LABELS.get(str(reason or ""), str(reason or "unknown"))


def get_conversation_manual_ai_state(lead: CRMLead | None) -> ConversationManualAIState:
    if lead is None:
        return ConversationManualAIState(
            manual_enabled=False,
            source="crm_leads.ia_ativa",
            reason="lead_not_found",
        )
    return ConversationManualAIState(
        manual_enabled=bool(getattr(lead, "ia_ativa", True)),
        source="crm_leads.ia_ativa",
        updated_at=getattr(lead, "criado_em", None),
        actor=None,
        reason="crm_leads.ia_ativa",
    )


def set_conversation_ai_manual_state(
    lead: CRMLead,
    *,
    enabled: bool,
    actor: str | None = None,
    reason: str = "manual_endpoint",
) -> ConversationManualAIState:
    lead.ia_ativa = bool(enabled)
    if enabled and str(getattr(lead, "status_atendimento", "") or "").lower() in {"manual", "aguardando_humano", "handoff"}:
        lead.status_atendimento = "aberto"
        lead.bot_pausado_ate = None
    dados = dict(getattr(lead, "dados_adicionais", None) or {})
    dados["ai_manual_state"] = {
        "enabled": bool(enabled),
        "source": "manual",
        "actor": str(actor or "") or None,
        "reason": reason,
        "changed_at": iso(utcnow()),
    }
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    return ConversationManualAIState(
        manual_enabled=bool(enabled),
        source="crm_leads.ia_ativa",
        updated_at=utcnow(),
        actor=str(actor or "") or None,
        reason=reason,
    )


def initialize_conversation_ai_from_campaign(
    lead: CRMLead,
    *,
    enabled: bool,
    campaign_id: str | None = None,
    created_new_conversation: bool = False,
) -> ConversationManualAIState:
    """Initialize the conversation gate without overriding a manual OFF."""
    dados = dict(getattr(lead, "dados_adicionais", None) or {})
    previous = dados.get("ai_manual_state") if isinstance(dados.get("ai_manual_state"), dict) else {}
    previous_source = str(previous.get("source") or "").strip().lower()
    previous_enabled = previous.get("enabled")
    previous_reason = str(previous.get("reason") or "").strip().lower()
    if (
        bool(enabled)
        and not bool(created_new_conversation)
        and previous_enabled is False
        and (
        previous_source == "manual" or previous_reason in {"inbox_toggle", "manual_endpoint"}
        )
    ):
        return get_conversation_manual_ai_state(lead)

    lead.ia_ativa = bool(enabled)
    dados["ai_manual_state"] = {
        "enabled": bool(enabled),
        "source": "campaign",
        "campaign_id": str(campaign_id or "") or None,
        "reason": "campaign_initialization",
        "changed_at": iso(utcnow()),
    }
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    return ConversationManualAIState(
        manual_enabled=bool(enabled),
        source="campaign",
        updated_at=utcnow(),
        actor=None,
        reason="campaign_initialization",
    )


async def resolve_conversation_ai_state(
    db: AsyncSession,
    *,
    empresa: Empresa,
    lead: CRMLead | None,
    conversation: ChannelConversation | None,
    connection_id: str | None,
    provider: str | None,
    phone: str | None,
    trigger: str | None = None,
    now: datetime | None = None,
    persist: bool = True,
) -> ConversationAIState:
    return await evaluate_conversation_ai_gate(
        db,
        empresa=empresa,
        lead=lead,
        conversation=conversation,
        connection_id=connection_id,
        provider=provider,
        phone=phone,
        trigger=trigger,
        now=now,
        persist=persist,
    )


async def evaluate_conversation_ai_gate(
    db: AsyncSession,
    *,
    empresa: Empresa,
    lead: CRMLead | None,
    conversation: ChannelConversation | None,
    connection_id: str | None,
    provider: str | None,
    phone: str | None,
    trigger: str | None = None,
    now: datetime | None = None,
    persist: bool = True,
) -> ConversationAIState:
    current = now or utcnow()
    if lead is None:
        return ConversationAIState("configuration_error", "internal_error", connection_id=connection_id, provider=provider)

    provider_norm = str(provider or "").lower()

    manual_state = get_conversation_manual_ai_state(lead)
    if not manual_state.manual_enabled:
        return ConversationAIState(
            "blocked",
            "ai_manual_disabled",
            manual_ai_enabled=False,
            human_state="manual_disabled",
            connection_id=connection_id,
            provider=provider_norm,
        )
    if str(getattr(lead, "status_atendimento", "") or "").lower() in {"manual", "aguardando_humano", "handoff"}:
        return ConversationAIState(
            "blocked",
            "human_takeover",
            manual_ai_enabled=True,
            human_state="human_takeover",
            connection_id=connection_id,
            provider=provider_norm,
        )
    paused_until = getattr(lead, "bot_pausado_ate", None)
    if paused_until and paused_until > current:
        return ConversationAIState(
            "blocked",
            "human_takeover",
            manual_ai_enabled=True,
            human_state="bot_paused",
            connection_id=connection_id,
            provider=provider_norm,
        )

    return ConversationAIState(
        "active",
        "allowed",
        manual_ai_enabled=True,
        human_state="none",
        connection_state="available",
        provider_state=provider_norm,
        connection_id=connection_id,
        provider=provider_norm,
    )
