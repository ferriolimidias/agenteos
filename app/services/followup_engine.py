from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.time_serialization import serialize_utc_timestamp
from app.api.utils import is_ai_blocked
from app.agents.atendimento.prompt_builder import build_atendimento_system_prompt
from app.agents.llm import OpenAIAgentLLM
from app.agents.tenant_config import build_tenant_agent_config
from app.core.prompt_placeholders import substituir_placeholders_nome_lead_em_texto
from app.core.secret_sanitizer import sanitize_for_log
from app.services.campaign_central_policy import PolicyInput, evaluate_message_policy
from app.services.channel_factory import despachar_mensagem
from app.services.followup_delivery_policy import evaluate_followup_delivery
from app.services.followup_rules import (
    FOLLOWUP_ANCHOR_AD_ENTRY,
    FOLLOWUP_ANCHOR_CONTACT_CREATED,
    FOLLOWUP_ANCHOR_FIXED_DATETIME,
    FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
    FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
    FOLLOWUP_ANCHOR_OTHER_EVENT,
    FOLLOWUP_ANCHOR_TAG_APPLIED,
    FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL,
    FOLLOWUP_MESSAGE_MODE_FIXED_TEXT,
    FOLLOWUP_MESSAGE_MODE_META_TEMPLATE,
    FOLLOWUP_MODE_CONVERSATION_RESUME,
    FOLLOWUP_MODE_SCHEDULED_MESSAGE,
    FollowupRuleSpec,
)
from app.services.meta_templates import get_template_for_tenant, parse_template_components, validate_template_parameters
from app.services.operational_identity import ensure_operational_conversation
from app.services.outbox_service import deterministic_idempotency_key, dispatch_outbox_now, enqueue_message, upsert_followup_execution
from app.services.media_storage import MediaValidationError, ensure_voice_variant, resolve_storage_path, storage_root
from db.database import AsyncSessionLocal
from db.models import (
    CRMLead,
    ChannelContact,
    ChannelConversation,
    CommunicationConsent,
    ConfigFollowUp,
    Empresa,
    FollowupExecution,
    FollowupSequenceStep,
    LeadFollowUpLog,
    MensagemHistorico,
    MediaAsset,
    MetaMessageTemplate,
)

logger = logging.getLogger(__name__)
worker_logger = logging.getLogger("uvicorn.error")

FOLLOWUP_POLICY_VERSION = "followup_simplified_v1"
FOLLOWUP_PENDING_STATUSES = {"scheduled", "retry", "locked"}
FOLLOWUP_CANCELLED_STATUS = "cancelled"
FOLLOWUP_BLOCKED_STATUS = "stopped"
LEAD_HUMAN_STATES = {"manual", "aguardando_humano", "handoff"}
FOLLOWUP_MAX_PROCESSING_RETRIES = 3
FOLLOWUP_RETRY_BACKOFF_SECONDS = 60


@dataclass(frozen=True)
class FollowupContext:
    empresa_id: uuid.UUID
    lead: CRMLead
    contact: ChannelContact
    conversation: ChannelConversation
    conexao_id: uuid.UUID
    provider: str
    channel: str


def _safe_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


async def _resolve_followup_context(
    session: AsyncSession,
    *,
    lead: CRMLead,
    contact: ChannelContact | None = None,
    conversation: ChannelConversation | None = None,
    conexao_id: uuid.UUID | None = None,
    provider: str | None = None,
    channel: str | None = None,
) -> FollowupContext | None:
    empresa_id = _safe_uuid(getattr(lead, "empresa_id", None))
    if empresa_id is None:
        return None

    if contact is None:
        result_contact = await session.execute(
            select(ChannelContact)
            .where(
                ChannelContact.empresa_id == empresa_id,
                ChannelContact.lead_id == lead.id,
            )
            .order_by(ChannelContact.last_seen_at.desc(), ChannelContact.criado_em.desc())
            .limit(1)
        )
        contact = result_contact.scalars().first()

    if conversation is None:
        result_conversation = await session.execute(
            select(ChannelConversation)
            .where(
                ChannelConversation.empresa_id == empresa_id,
                ChannelConversation.lead_id == lead.id,
            )
            .order_by(ChannelConversation.last_message_at.desc(), ChannelConversation.criado_em.desc())
            .limit(1)
        )
        conversation = result_conversation.scalars().first()

    if contact is None and conversation is not None:
        contact = conversation.contact
    if conversation is None and contact is not None:
        result_conversation = await session.execute(
            select(ChannelConversation)
            .where(
                ChannelConversation.empresa_id == empresa_id,
                ChannelConversation.contact_id == contact.id,
                ChannelConversation.lead_id == lead.id,
            )
            .order_by(ChannelConversation.last_message_at.desc(), ChannelConversation.criado_em.desc())
            .limit(1)
        )
        conversation = result_conversation.scalars().first()

    if not contact or not conversation:
        return None

    conexao_uuid = conexao_id or _safe_uuid(getattr(conversation, "conexao_id", None)) or _safe_uuid(getattr(contact, "conexao_id", None))
    if conexao_uuid is None:
        return None

    provider_norm = str(provider or getattr(conversation, "provider", None) or getattr(contact, "provider", None) or "").strip()
    channel_norm = str(channel or getattr(conversation, "channel", None) or getattr(contact, "channel", None) or "").strip()
    if not provider_norm or not channel_norm:
        return None

    return FollowupContext(
        empresa_id=empresa_id,
        lead=lead,
        contact=contact,
        conversation=conversation,
        conexao_id=conexao_uuid,
        provider=provider_norm,
        channel=channel_norm,
    )


def _lead_meta_attribution_captured_at(lead: CRMLead) -> datetime | None:
    dados = getattr(lead, "dados_adicionais", None)
    if not isinstance(dados, dict):
        return None
    attribution = dados.get("meta_attribution")
    if not isinstance(attribution, dict):
        return None
    touch = attribution.get("last_touch") if isinstance(attribution.get("last_touch"), dict) else attribution.get("first_touch")
    if not isinstance(touch, dict):
        return None
    captured_at = touch.get("captured_at")
    if not captured_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _lead_human_state_active(lead: CRMLead | None, now: datetime | None = None) -> bool:
    if not lead:
        return False
    status = str(getattr(lead, "status_atendimento", "") or "").strip().lower()
    if status in LEAD_HUMAN_STATES:
        return True
    paused_until = getattr(lead, "bot_pausado_ate", None)
    if paused_until and (now or datetime.utcnow()) < paused_until:
        return True
    return False


def _consent_status_is_blocking(status: str | None) -> bool:
    return str(status or "").strip().lower() in {"opted_out", "opt_out", "blocked", "revoked"}


async def _current_consent_status(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: str,
    provider: str,
    conexao_id: uuid.UUID | None = None,
) -> str:
    conditions = [
        CommunicationConsent.empresa_id == empresa_id,
        CommunicationConsent.contact_id == contact_id,
        CommunicationConsent.channel == str(channel).strip().lower(),
        CommunicationConsent.provider == str(provider).strip().lower(),
    ]
    if conexao_id is not None:
        conditions.append(CommunicationConsent.conexao_id == conexao_id)
    result = await session.execute(select(CommunicationConsent.status).where(and_(*conditions)).limit(1))
    return str(result.scalar_one_or_none() or "unknown")


async def _load_followup_rows(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    followup_ids: list[uuid.UUID] | None = None,
) -> list[tuple[ConfigFollowUp, dict[str, Any] | None, FollowupRuleSpec]]:
    stmt = select(ConfigFollowUp).where(ConfigFollowUp.empresa_id == empresa_id, ConfigFollowUp.ativo.is_(True))
    if followup_ids:
        stmt = stmt.where(ConfigFollowUp.id.in_(followup_ids))
    result = await session.execute(stmt.order_by(ConfigFollowUp.nome.asc(), ConfigFollowUp.criado_em.asc()))
    items = result.scalars().all()
    if not items:
        return []

    step_rows = (
        await session.execute(
            select(FollowupSequenceStep)
            .where(
                and_(
                    FollowupSequenceStep.empresa_id == empresa_id,
                    FollowupSequenceStep.followup_id.in_([item.id for item in items]),
                )
            )
            .order_by(FollowupSequenceStep.step_order.asc(), FollowupSequenceStep.created_at.asc())
        )
    ).scalars().all()
    step_map: dict[str, dict[str, Any]] = {}
    for row in step_rows:
        step_map.setdefault(str(row.followup_id), {
            "id": row.id,
            "empresa_id": row.empresa_id,
            "followup_id": row.followup_id,
            "step_order": row.step_order,
            "status": row.status,
            "trigger_tag_id": row.trigger_tag_id,
            "delay_minutes": row.delay_minutes,
            "timezone": row.timezone,
            "ad_window_mode": row.ad_window_mode,
            "message_mode": getattr(row, "message_mode", None),
            "message_free_text": row.message_free_text,
            "content_type": row.content_type,
            "media_asset_id": row.media_asset_id,
            "send_as_voice": row.send_as_voice,
            "audio_caption": row.audio_caption,
            "template_id": row.template_id,
            "variable_mapping_json": row.variable_mapping_json,
            "execute_conditions_json": row.execute_conditions_json,
            "cancel_conditions_json": row.cancel_conditions_json,
            "post_send_tag_id": row.post_send_tag_id,
            "actions_json": row.actions_json,
            "send_window_enabled": getattr(row, "send_window_enabled", None),
            "send_window_start": getattr(row, "send_window_start", None),
            "send_window_end": getattr(row, "send_window_end", None),
            "limit_to_free_entry_window": getattr(row, "limit_to_free_entry_window", None),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        })

    return [
        (item, step_map.get(str(item.id)), FollowupRuleSpec.from_row(item, step_map.get(str(item.id))))
        for item in items
    ]


def _conversation_anchor_message_id(message: MensagemHistorico | None) -> str | None:
    if not message:
        return None
    return str(getattr(message, "external_message_id", "") or getattr(message, "id", "") or "").strip() or None


def _render_followup_text(spec: FollowupRuleSpec, *, lead: CRMLead) -> str:
    text = substituir_placeholders_nome_lead_em_texto(str(spec.message_content or "").strip(), str(lead.nome_contato or ""))
    return text.strip()


def _clean_followup_ai_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    text = "\n".join(lines).strip()
    if len(text) > 700:
        text = text[:700].rsplit(" ", 1)[0].strip()
    return text


async def _generate_contextual_followup_text(
    session: AsyncSession,
    *,
    ctx: FollowupContext,
    spec: FollowupRuleSpec,
) -> str:
    result_empresa = await session.execute(select(Empresa).where(Empresa.id == ctx.empresa_id))
    empresa = result_empresa.scalars().first()
    if not empresa:
        raise RuntimeError("tenant_not_found")
    tenant_config = build_tenant_agent_config(empresa)
    tenant_config["empresa_id"] = str(ctx.empresa_id)
    tenant_config["_openai_api_key"] = str(
        getattr(empresa, "openai_api_key", "")
        or ((getattr(empresa, "credenciais_canais", {}) or {}).get("openai_api_key"))
        or ((getattr(empresa, "credenciais_canais", {}) or {}).get("api_key_openai"))
        or ""
    ).strip() or None

    history_result = await session.execute(
        select(MensagemHistorico)
        .where(
            MensagemHistorico.empresa_id == ctx.empresa_id,
            MensagemHistorico.lead_id == ctx.lead.id,
            MensagemHistorico.channel_conversation_id == ctx.conversation.id,
        )
        .order_by(MensagemHistorico.criado_em.desc(), MensagemHistorico.id.desc())
        .limit(12)
    )
    recent_messages = list(reversed(history_result.scalars().all()))
    conversation_lines: list[str] = []
    llm_messages: list[dict[str, str]] = []
    for message in recent_messages:
        text = str(getattr(message, "texto", "") or "").strip()
        if not text:
            continue
        role = "assistant" if bool(getattr(message, "from_me", False)) else "user"
        label = "Agente" if role == "assistant" else "Lead"
        conversation_lines.append(f"{label}: {text[:900]}")
        llm_messages.append({"role": role, "content": text[:1200]})

    instruction = str(spec.message_content or "").strip()
    lead_name = str(getattr(ctx.lead, "nome_contato", "") or "").strip()
    system_prompt = (
        build_atendimento_system_prompt(tenant_config, memory=str(getattr(ctx.lead, "historico_resumo", "") or ""))
        + "\n\n"
        "Tarefa atual: gerar somente UMA mensagem curta de retomada de conversa para WhatsApp.\n"
        "Use o contexto real abaixo e continue do ponto em que a conversa parou.\n"
        "Nao repita pergunta ja respondida. Nao invente preco, oferta, urgencia, promessa ou informacao.\n"
        "Nao diga que e automacao, follow-up ou processo interno.\n"
        "A mensagem deve ser natural, curta, em portugues, e normalmente terminar com uma pergunta simples.\n"
        "Retorne apenas o texto final da mensagem, sem explicacoes."
    )
    llm_messages.append(
        {
            "role": "user",
            "content": (
                f"Nome do lead: {lead_name or '(nao informado)'}\n\n"
                f"Instrucao configurada do follow-up:\n{instruction}\n\n"
                "Historico recente:\n"
                + ("\n".join(conversation_lines) if conversation_lines else "(sem historico recente)")
                + "\n\nGere a mensagem unica de retomada agora."
            ),
        }
    )
    llm = OpenAIAgentLLM.from_config(tenant_config)
    output = await llm.generate(system_prompt=system_prompt, messages=llm_messages, tools_context={"followup_mode": "ai_contextual"})
    text = _clean_followup_ai_text(output)
    if not text:
        raise RuntimeError("ai_followup_empty")
    return text


def _resolve_variable_value(
    *,
    spec_item: dict[str, Any],
    lead: CRMLead,
    contact: ChannelContact | None,
    conversation: ChannelConversation | None,
) -> str:
    source = str(spec_item.get("value_source") or "fixed").strip().lower()
    raw_value = spec_item.get("value")
    if source in {"fixed", "literal", "text"}:
        return str(raw_value or "").strip()

    field_name = str(raw_value or "").strip()
    if not field_name:
        return ""

    if source in {"crm_field", "lead_field"}:
        if field_name == "nome_contato":
            return str(getattr(lead, "nome_contato", "") or "").strip()
        if field_name == "telefone_contato":
            return str(getattr(lead, "telefone_contato", "") or "").strip()
        if field_name == "status_atendimento":
            return str(getattr(lead, "status_atendimento", "") or "").strip()
        if hasattr(lead, field_name):
            return str(getattr(lead, field_name, "") or "").strip()
        return ""

    if source in {"contact_field", "channel_contact_field"} and contact is not None:
        if hasattr(contact, field_name):
            return str(getattr(contact, field_name, "") or "").strip()
        return ""

    if source in {"conversation_field", "channel_conversation_field"} and conversation is not None:
        if hasattr(conversation, field_name):
            value = getattr(conversation, field_name, "")
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value or "").strip()
        return ""

    return str(raw_value or "").strip()


def _build_template_parameters(
    spec: FollowupRuleSpec,
    *,
    lead: CRMLead,
    contact: ChannelContact | None,
    conversation: ChannelConversation | None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for item in spec.variable_mapping:
        try:
            position = int(item.get("position"))
        except (TypeError, ValueError):
            continue
        key = str(position)
        parameters[key] = _resolve_variable_value(spec_item=item, lead=lead, contact=contact, conversation=conversation)
        if not parameters[key]:
            parameters[key] = ""
    return parameters


def _template_components_from_template(template: MetaMessageTemplate, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    parsed = parse_template_components([component.raw_component_json for component in template.components])
    if parsed.requires_media or parsed.has_dynamic_url or parsed.has_carousel or parsed.has_authentication:
        raise ValueError("template_complexo_nao_suportado")

    if not parsed.placeholders:
        return components

    # A plataforma oficial espera parâmetros na ordem dos placeholders.
    ordered = sorted(parsed.placeholders, key=lambda spec: (spec.component_order, spec.name))
    grouped: dict[int, list[dict[str, Any]]] = {}
    for placeholder in ordered:
        raw_value = parameters.get(placeholder.name)
        if raw_value in (None, "") and placeholder.name.isdigit():
            raw_value = parameters.get(str(int(placeholder.name)))
        if raw_value in (None, ""):
            raise ValueError("template_parameters_invalidos")
        grouped.setdefault(placeholder.component_order, []).append({"type": "text", "text": str(raw_value)})

    for component in template.components:
        component_type = str(component.component_type or "").strip().upper()
        if component_type == "BODY":
            params = grouped.get(component.component_order) or []
            components.append({"type": "body", "parameters": params})
        elif component_type == "FOOTER":
            continue
        elif component_type == "HEADER":
            if str(component.format or "").strip():
                raise ValueError("template_media_nao_suportada")
        elif component_type == "BUTTONS":
            continue
        else:
            raise ValueError("template_complexo_nao_suportado")

    return components


async def _build_template_payload(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    spec: FollowupRuleSpec,
    lead: CRMLead,
    contact: ChannelContact,
    conversation: ChannelConversation,
) -> dict[str, Any]:
    if not spec.template_id:
        raise ValueError("template_nao_configurado")
    template = await get_template_for_tenant(
        session,
        empresa_id=empresa_id,
        conexao_id=conexao_id,
        template_id=uuid.UUID(str(spec.template_id)),
    )
    if str(template.normalized_status or "").lower() != "approved" or template.archived_at or template.stale_at:
        raise ValueError("template_nao_aprovado")
    parameters = _build_template_parameters(spec, lead=lead, contact=contact, conversation=conversation)
    components = _template_components_from_template(template, parameters)
    validation = validate_template_parameters(parse_template_components([component.raw_component_json for component in template.components]), parameters)
    if not validation["valid"]:
        raise ValueError("template_parameters_invalidos")
    return {
        "name": template.name,
        "language": {"code": template.language},
        "components": components,
    }


async def _cancel_execution(
    session: AsyncSession,
    execution: FollowupExecution,
    *,
    reason: str,
) -> None:
    if str(execution.status or "").lower() in {"sent", "failed", "cancelled"}:
        return
    execution.status = FOLLOWUP_CANCELLED_STATUS
    execution.cancelled_at = execution.cancelled_at or datetime.utcnow()
    execution.stop_reason = reason[:120]
    execution.completed_at = execution.completed_at or execution.cancelled_at
    logger.info(
        "FOLLOWUP_CANCEL execution_id=%s rule_id=%s empresa_id=%s reason=%s",
        execution.id,
        execution.followup_id,
        execution.empresa_id,
        sanitize_for_log(reason),
    )


async def _cancel_existing_pending(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    followup_id: uuid.UUID | None = None,
    rule_ids: list[uuid.UUID] | None = None,
    reason: str,
) -> int:
    conditions = [FollowupExecution.empresa_id == empresa_id, FollowupExecution.status.in_(FOLLOWUP_PENDING_STATUSES)]
    if lead_id is not None:
        conditions.append(FollowupExecution.lead_id == lead_id)
    if contact_id is not None:
        conditions.append(FollowupExecution.contact_id == contact_id)
    if conversation_id is not None:
        conditions.append(FollowupExecution.conversation_id == conversation_id)
    if followup_id is not None:
        conditions.append(FollowupExecution.followup_id == followup_id)
    if rule_ids:
        conditions.append(FollowupExecution.followup_id.in_(rule_ids))
    result = await session.execute(select(FollowupExecution).where(and_(*conditions)))
    count = 0
    for execution in result.scalars().all():
        await _cancel_execution(session, execution, reason=reason)
        count += 1
    return count


async def _upsert_execution_for_rule(
    session: AsyncSession,
    *,
    ctx: FollowupContext,
    followup_id: uuid.UUID,
    spec: FollowupRuleSpec,
    anchor_kind: str,
    anchor_message_id: str | None = None,
    anchor_tag_id: str | None = None,
    anchor_event_id: str | None = None,
    anchor_at: datetime | None = None,
    replace_previous: bool = False,
) -> FollowupExecution | None:
    now = datetime.utcnow()
    anchor_at = anchor_at or now
    if replace_previous and spec.is_conversation_resume:
        await _cancel_existing_pending(
            session,
            empresa_id=ctx.empresa_id,
            lead_id=ctx.lead.id,
            contact_id=ctx.contact.id,
            conversation_id=ctx.conversation.id,
            followup_id=followup_id,
            reason="outbound_replaced",
        )

    anchor_key = anchor_event_id or anchor_message_id or anchor_tag_id or anchor_at.isoformat()
    idem = deterministic_idempotency_key(
        "followup",
        ctx.empresa_id,
        ctx.lead.id,
        ctx.contact.id,
        ctx.conversation.id,
        spec.mode,
        spec.anchor_type,
        spec.campaign_or_event_id or "",
        anchor_key,
        spec.template_id or "",
        spec.message_content or "",
    )
    execution = await upsert_followup_execution(
        session,
        empresa_id=ctx.empresa_id,
        followup_id=followup_id,
        lead_id=ctx.lead.id,
        contact_id=ctx.contact.id,
        conversation_id=ctx.conversation.id,
        conexao_id=ctx.conexao_id,
        provider=ctx.provider,
        channel=ctx.channel,
        trigger=anchor_kind,
        scheduled_at=anchor_at + timedelta(minutes=max(0, int(spec.delay_minutes or 0))),
        idempotency_key=idem,
        template_id=uuid.UUID(str(spec.template_id)) if spec.template_id else None,
    )
    execution.triggered_at = execution.triggered_at or anchor_at
    execution.policy_version = FOLLOWUP_POLICY_VERSION
    execution.policy_snapshot_json = {
        "mode": spec.mode,
        "anchor_type": spec.anchor_type,
        "anchor_kind": anchor_kind,
        "anchor_message_id": anchor_message_id,
        "anchor_tag_id": anchor_tag_id,
        "anchor_event_id": anchor_event_id,
        "anchor_at": anchor_at.isoformat(),
        "delay_minutes": spec.delay_minutes,
        "template_id": spec.template_id,
        "message_mode": spec.message_mode,
        "send_window_enabled": bool(spec.send_window_enabled),
        "send_window_start": spec.send_window_start,
        "send_window_end": spec.send_window_end,
        "timezone": spec.timezone,
        "limit_to_free_entry_window": bool(spec.limit_to_free_entry_window),
        "cancel_tag_ids": list(spec.cancel_tag_ids),
        "cancel_on_inbound": bool(spec.cancel_on_inbound),
        "cancel_on_human": bool(spec.cancel_on_human),
        "cancel_on_opt_out": bool(spec.cancel_on_opt_out),
        "cancel_on_event_end": bool(spec.cancel_on_event_end),
    }
    if anchor_kind == "ad_entry":
        execution.ad_entry_started_at = execution.ad_entry_started_at or anchor_at
        execution.ad_entry_window_state = execution.ad_entry_window_state or "unknown"
    if spec.anchor_type == FOLLOWUP_ANCHOR_TAG_APPLIED and anchor_tag_id:
        execution.trigger_tag_id = _safe_uuid(anchor_tag_id)
    return execution


async def registrar_followup_contact_created(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead: CRMLead,
    contact: ChannelContact | None = None,
    conversation: ChannelConversation | None = None,
    conexao_id: uuid.UUID | None = None,
    provider: str | None = None,
    channel: str | None = None,
    created_at: datetime | None = None,
) -> int:
    ctx = await _resolve_followup_context(
        session,
        lead=lead,
        contact=contact,
        conversation=conversation,
        conexao_id=conexao_id,
        provider=provider,
        channel=channel,
    )
    if not ctx:
        return 0
    rows = await _load_followup_rows(session, empresa_id=empresa_id)
    count = 0
    anchor_at = created_at or getattr(lead, "criado_em", None) or datetime.utcnow()
    for item, _, spec in rows:
        if spec.mode != FOLLOWUP_MODE_SCHEDULED_MESSAGE:
            continue
        if spec.anchor_type not in {FOLLOWUP_ANCHOR_CONTACT_CREATED, FOLLOWUP_ANCHOR_FIXED_DATETIME}:
            continue
        if spec.anchor_type == FOLLOWUP_ANCHOR_FIXED_DATETIME and not spec.activation_at:
            continue
        event_at = spec.activation_at if spec.anchor_type == FOLLOWUP_ANCHOR_FIXED_DATETIME else anchor_at
        execution = await _upsert_execution_for_rule(
            session,
            ctx=ctx,
            followup_id=item.id,
            spec=spec,
            anchor_kind="contact_created" if spec.anchor_type == FOLLOWUP_ANCHOR_CONTACT_CREATED else "fixed_datetime",
            anchor_event_id=str(lead.id) if spec.anchor_type == FOLLOWUP_ANCHOR_CONTACT_CREATED else str(spec.activation_at.isoformat()),
            anchor_at=event_at,
        )
        if execution:
            count += 1
    return count


async def registrar_followup_tag_event(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead: CRMLead,
    contact: ChannelContact | None = None,
    conversation: ChannelConversation | None = None,
    conexao_id: uuid.UUID | None = None,
    provider: str | None = None,
    channel: str | None = None,
    tag_id: uuid.UUID,
    action: str = "applied",
    occurred_at: datetime | None = None,
    source_event_id: str | None = None,
) -> dict[str, int]:
    rows = await _load_followup_rows(session, empresa_id=empresa_id)
    cancelled = 0
    scheduled = 0
    tag_text = str(tag_id)
    ctx = await _resolve_followup_context(
        session,
        lead=lead,
        contact=contact,
        conversation=conversation,
        conexao_id=conexao_id,
        provider=provider,
        channel=channel,
    )

    # Cancelamento imediato por tags configuradas.
    for item, _, spec in rows:
        if not spec.cancel_tag_ids:
            continue
        if tag_text not in set(spec.cancel_tag_ids):
            continue
        cancelled += await _cancel_existing_pending(
            session,
            empresa_id=empresa_id,
            lead_id=lead.id,
            contact_id=ctx.contact.id if ctx else None,
            conversation_id=ctx.conversation.id if ctx else None,
            followup_id=item.id,
            reason="cancel_tag_applied",
        )

    # Âncora por tag aplicada.
    if action == "applied" and ctx is not None:
        for item, _, spec in rows:
            if spec.mode != FOLLOWUP_MODE_SCHEDULED_MESSAGE:
                continue
            if spec.anchor_type != FOLLOWUP_ANCHOR_TAG_APPLIED:
                continue
            target_tag = spec.trigger_tag_id or spec.campaign_or_event_id
            if target_tag and str(target_tag) != tag_text:
                continue
            anchor_at = occurred_at or datetime.utcnow()
            execution = await _upsert_execution_for_rule(
                session,
                ctx=ctx,
                followup_id=item.id,
                spec=spec,
                anchor_kind="tag_applied",
                anchor_tag_id=tag_text,
                anchor_event_id=source_event_id,
                anchor_at=anchor_at,
            )
            if execution:
                scheduled += 1

    return {"scheduled": scheduled, "cancelled": cancelled}


async def registrar_followup_message_event(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead: CRMLead,
    contact: ChannelContact | None = None,
    conversation: ChannelConversation | None = None,
    conexao_id: uuid.UUID | None = None,
    provider: str | None = None,
    channel: str | None = None,
    from_me: bool,
    message: MensagemHistorico | None = None,
    message_at: datetime | None = None,
) -> dict[str, int]:
    ctx = await _resolve_followup_context(
        session,
        lead=lead,
        contact=contact,
        conversation=conversation,
        conexao_id=conexao_id,
        provider=provider,
        channel=channel,
    )
    if not ctx:
        return {"scheduled": 0, "cancelled": 0}

    rows = await _load_followup_rows(session, empresa_id=empresa_id)
    message_at = message_at or getattr(message, "criado_em", None) or datetime.utcnow()
    anchor_message_id = _conversation_anchor_message_id(message)
    cancelled = 0
    scheduled = 0

    # Inbound cancela retomada de conversa quando configurado.
    if not from_me:
        for item, _, spec in rows:
            if not spec.cancel_on_inbound:
                continue
            cancelled += await _cancel_existing_pending(
                session,
                empresa_id=empresa_id,
                lead_id=lead.id,
                contact_id=ctx.contact.id,
                conversation_id=ctx.conversation.id,
                followup_id=item.id,
                reason="inbound_received",
            )

    # Outbound reinicia/agenda o modo "retomar conversa parada".
    if from_me:
        for item, _, spec in rows:
            if spec.mode != FOLLOWUP_MODE_CONVERSATION_RESUME:
                continue
            cancelled += await _cancel_existing_pending(
                session,
                empresa_id=empresa_id,
                lead_id=lead.id,
                contact_id=ctx.contact.id,
                conversation_id=ctx.conversation.id,
                followup_id=item.id,
                reason="outbound_replaced",
            )
            execution = await _upsert_execution_for_rule(
                session,
                ctx=ctx,
                followup_id=item.id,
                spec=spec,
                anchor_kind="outbound_message",
                anchor_message_id=anchor_message_id,
                anchor_event_id=anchor_message_id,
                anchor_at=message_at,
                replace_previous=False,
            )
            if execution:
                scheduled += 1
        return {"scheduled": scheduled, "cancelled": cancelled}

    # Inbound agenda o modo programado quando a âncora é a primeira mensagem.
    for item, _, spec in rows:
        if spec.mode != FOLLOWUP_MODE_SCHEDULED_MESSAGE:
            continue
        if spec.anchor_type == FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE:
            existing = await session.execute(
                select(FollowupExecution.id).where(
                    FollowupExecution.empresa_id == empresa_id,
                    FollowupExecution.followup_id == item.id,
                    FollowupExecution.lead_id == lead.id,
                    FollowupExecution.conversation_id == ctx.conversation.id,
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            anchor_at = message_at
            execution = await _upsert_execution_for_rule(
                session,
                ctx=ctx,
                followup_id=item.id,
                spec=spec,
                anchor_kind="first_inbound_message",
                anchor_message_id=anchor_message_id,
                anchor_event_id=anchor_message_id,
                anchor_at=anchor_at,
            )
            if execution:
                scheduled += 1
        elif spec.anchor_type == FOLLOWUP_ANCHOR_AD_ENTRY:
            anchor_at = _lead_meta_attribution_captured_at(lead)
            if not anchor_at:
                continue
            existing = await session.execute(
                select(FollowupExecution.id).where(
                    FollowupExecution.empresa_id == empresa_id,
                    FollowupExecution.followup_id == item.id,
                    FollowupExecution.lead_id == lead.id,
                    FollowupExecution.conversation_id == ctx.conversation.id,
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            execution = await _upsert_execution_for_rule(
                session,
                ctx=ctx,
                followup_id=item.id,
                spec=spec,
                anchor_kind="ad_entry",
                anchor_event_id=str(spec.campaign_or_event_id or anchor_message_id or ""),
                anchor_at=anchor_at,
            )
            if execution:
                scheduled += 1
        elif spec.anchor_type == FOLLOWUP_ANCHOR_FIXED_DATETIME and spec.activation_at:
            existing = await session.execute(
                select(FollowupExecution.id).where(
                    FollowupExecution.empresa_id == empresa_id,
                    FollowupExecution.followup_id == item.id,
                    FollowupExecution.lead_id == lead.id,
                    FollowupExecution.conversation_id == ctx.conversation.id,
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            execution = await _upsert_execution_for_rule(
                session,
                ctx=ctx,
                followup_id=item.id,
                spec=spec,
                anchor_kind="fixed_datetime",
                anchor_event_id=str(spec.activation_at.isoformat()),
                anchor_at=spec.activation_at,
            )
            if execution:
                scheduled += 1

    return {"scheduled": scheduled, "cancelled": cancelled}


async def registrar_followup_event_end(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead: CRMLead,
    contact: ChannelContact,
    conversation: ChannelConversation,
    conexao_id: uuid.UUID,
    provider: str,
    channel: str,
    campaign_or_event_id: str | None,
) -> int:
    rows = await _load_followup_rows(session, empresa_id=empresa_id)
    count = 0
    for item, _, spec in rows:
        if not spec.cancel_on_event_end:
            continue
        if spec.campaign_or_event_id and campaign_or_event_id and str(spec.campaign_or_event_id) != str(campaign_or_event_id):
            continue
        count += await _cancel_existing_pending(
            session,
            empresa_id=empresa_id,
            lead_id=lead.id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            followup_id=item.id,
            reason="event_ended",
        )
    return count


async def _followup_context_for_execution(session: AsyncSession, execution: FollowupExecution) -> FollowupContext | None:
    lead = execution.lead
    conversation = execution.conversation
    contact = execution.contact
    if not lead or not conversation or not contact:
        return None
    return FollowupContext(
        empresa_id=execution.empresa_id,
        lead=lead,
        contact=contact,
        conversation=conversation,
        conexao_id=execution.conexao_id,
        provider=str(execution.provider or "meta_whatsapp"),
        channel=str(execution.channel or "whatsapp"),
    )


async def _dispatch_followup_execution(
    session: AsyncSession,
    *,
    execution: FollowupExecution,
    spec: FollowupRuleSpec,
) -> bool:
    ctx = await _followup_context_for_execution(session, execution)
    if not ctx:
        execution.status = "failed"
        execution.failed_at = datetime.utcnow()
        execution.completed_at = execution.failed_at
        execution.stop_reason = "missing_context"
        return False

    lead = ctx.lead
    conversation = ctx.conversation
    contact = ctx.contact
    now = datetime.utcnow()

    if _lead_human_state_active(lead, now=now):
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "human_takeover"
        logger.info(
            "FOLLOWUP_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=human_takeover",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
        )
        return False

    consent_status = await _current_consent_status(
        session,
        empresa_id=ctx.empresa_id,
        contact_id=contact.id,
        channel=ctx.channel,
        provider=ctx.provider,
        conexao_id=ctx.conexao_id,
    )
    if _consent_status_is_blocking(consent_status):
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "opt_out"
        logger.info(
            "FOLLOWUP_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=opt_out",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
        )
        return False

    if spec.cancel_on_inbound and conversation.last_inbound_at and execution.triggered_at and conversation.last_inbound_at > execution.triggered_at:
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "inbound_received"
        logger.info(
            "FOLLOWUP_CANCEL execution_id=%s rule_id=%s empresa_id=%s reason=inbound_received",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
        )
        return False

    if spec.mode == FOLLOWUP_MODE_CONVERSATION_RESUME and conversation.last_outbound_at and execution.triggered_at and conversation.last_outbound_at > execution.triggered_at:
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "outbound_replaced"
        logger.info(
            "FOLLOWUP_CANCEL execution_id=%s rule_id=%s empresa_id=%s reason=outbound_replaced",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
        )
        return False

    delivery_decision = evaluate_followup_delivery(
        spec=spec,
        conversation=conversation,
        execution=execution,
        provider=ctx.provider,
        channel=ctx.channel,
        now=now,
    )
    execution.policy_snapshot_json = {
        **dict(execution.policy_snapshot_json or {}),
        "followup_delivery_decision": {
            "followup_id": str(execution.followup_id),
            "conversation_id": str(execution.conversation_id),
            "due_at": execution.scheduled_at.isoformat() if execution.scheduled_at else None,
            "now": now.isoformat(),
            "business_hours_allowed": bool(delivery_decision.business_hours_allowed),
            "csw_active": bool(delivery_decision.csw_active),
            "csw_expires_at": delivery_decision.csw_expires_at.isoformat() if delivery_decision.csw_expires_at else None,
            "fep_active": delivery_decision.fep_active,
            "fep_expires_at": delivery_decision.fep_expires_at.isoformat() if delivery_decision.fep_expires_at else None,
            "message_mode": delivery_decision.send_mode,
            "template_required": bool(delivery_decision.template_required),
            "eligible": bool(delivery_decision.eligible),
            "decision_reason": delivery_decision.reason,
            "next_attempt_at": delivery_decision.next_attempt_at.isoformat() if delivery_decision.next_attempt_at else None,
        },
    }
    if not delivery_decision.eligible:
        if delivery_decision.next_attempt_at is not None and delivery_decision.reason == "outside_business_hours":
            execution.status = "scheduled"
            execution.scheduled_at = delivery_decision.next_attempt_at
            execution.stop_reason = "outside_business_hours_rescheduled"
            logger.info(
                "FOLLOWUP_RESCHEDULE execution_id=%s rule_id=%s empresa_id=%s reason=outside_business_hours next_attempt_at=%s",
                execution.id,
                execution.followup_id,
                execution.empresa_id,
                delivery_decision.next_attempt_at.isoformat(),
            )
            return False
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = delivery_decision.reason[:120]
        logger.info(
            "FOLLOWUP_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=%s",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
            sanitize_for_log(delivery_decision.reason),
        )
        return False

    origin = "ctwa" if spec.anchor_type == FOLLOWUP_ANCHOR_AD_ENTRY or _lead_meta_attribution_captured_at(lead) else "unknown"
    ad_click_at = _lead_meta_attribution_captured_at(lead) if spec.anchor_type == FOLLOWUP_ANCHOR_AD_ENTRY else None
    policy = evaluate_message_policy(
        PolicyInput(
            provider=ctx.provider,
            channel=ctx.channel,
            origin=origin,
            execution_at=now,
            last_inbound_at=conversation.last_inbound_at,
            ad_click_at=ad_click_at,
            policy_version=FOLLOWUP_POLICY_VERSION,
        )
    )
    execution.policy_snapshot_json = {**dict(execution.policy_snapshot_json or {}), "delivery_policy": policy}
    execution.policy_version = FOLLOWUP_POLICY_VERSION

    if spec.message_mode != FOLLOWUP_MESSAGE_MODE_META_TEMPLATE and str(policy.get("message_mode") or "").strip().lower() == "template_required" and (
        not spec.template_id or spec.content_type in {"audio", "text_audio"}
    ):
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "audio_requires_freeform" if spec.content_type in {"audio", "text_audio"} else "delivery_policy_blocked"
        logger.info(
            "FOLLOWUP_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=%s",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
            execution.stop_reason,
        )
        return False

    message_text = ""
    if spec.message_mode == FOLLOWUP_MESSAGE_MODE_AI_CONTEXTUAL:
        try:
            message_text = await _generate_contextual_followup_text(session, ctx=ctx, spec=spec)
        except Exception as exc:
            execution.status = "failed"
            execution.failed_at = datetime.utcnow()
            execution.completed_at = execution.failed_at
            execution.stop_reason = str(sanitize_for_log(str(exc)))[:120] or "ai_followup_generation_failed"
            logger.warning(
                "FOLLOWUP_SEND_FAILURE execution_id=%s rule_id=%s empresa_id=%s reason=%s",
                execution.id,
                execution.followup_id,
                execution.empresa_id,
                execution.stop_reason,
            )
            return False
    elif spec.message_mode == FOLLOWUP_MESSAGE_MODE_FIXED_TEXT:
        message_text = _render_followup_text(spec, lead=lead)
    media_payload = None
    if spec.content_type in {"audio", "text_audio"}:
        asset_id = _safe_uuid(spec.media_asset_id)
        if asset_id is None:
            execution.status = FOLLOWUP_CANCELLED_STATUS
            execution.cancelled_at = now
            execution.completed_at = now
            execution.stop_reason = "audio_asset_missing"
            return False
        asset_result = await session.execute(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.empresa_id == ctx.empresa_id,
                MediaAsset.media_type == "audio",
                MediaAsset.status == "ready",
            )
        )
        asset = asset_result.scalars().first()
        if asset is None:
            execution.status = FOLLOWUP_CANCELLED_STATUS
            execution.cancelled_at = now
            execution.completed_at = now
            execution.stop_reason = "audio_asset_unavailable"
            return False
        try:
            if spec.send_as_voice:
                audio_path, audio_mime = ensure_voice_variant(asset=asset, empresa_id=str(ctx.empresa_id))
                metadata = dict(asset.metadata_json or {})
                # The storage helper validates the tenant boundary; retain the derived key for reuse.
                metadata["voice_storage_key"] = str(audio_path.relative_to(storage_root()))
                asset.metadata_json = metadata
                session.add(asset)
            else:
                audio_path, audio_mime = (
                    resolve_storage_path(str(asset.storage_key), empresa_id=str(ctx.empresa_id)), str(asset.mime_type)
                )
        except (MediaValidationError, OSError, subprocess.SubprocessError) as exc:
            execution.status = FOLLOWUP_CANCELLED_STATUS
            execution.cancelled_at = now
            execution.completed_at = now
            execution.stop_reason = str(exc)[:120]
            return False
        if not audio_path.is_file():
            execution.status = FOLLOWUP_CANCELLED_STATUS
            execution.cancelled_at = now
            execution.completed_at = now
            execution.stop_reason = "audio_file_missing"
            return False
        media_payload = {
            "path": str(audio_path),
            "mime_type": audio_mime,
            "filename": str(asset.original_name or "audio"),
            "asset_id": str(asset.id),
        }
    template_payload = None
    if spec.message_mode == FOLLOWUP_MESSAGE_MODE_META_TEMPLATE and not spec.template_id:
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "template_nao_configurado"
        return False
    if spec.message_mode == FOLLOWUP_MESSAGE_MODE_META_TEMPLATE and spec.content_type in {"audio", "text_audio"}:
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "template_does_not_authorize_audio"
        return False

    if spec.message_mode == FOLLOWUP_MESSAGE_MODE_META_TEMPLATE and spec.template_id:
        try:
            template_payload = await _build_template_payload(
                session,
                empresa_id=ctx.empresa_id,
                conexao_id=ctx.conexao_id,
                spec=spec,
                lead=lead,
                contact=contact,
                conversation=conversation,
            )
        except Exception as exc:
            execution.status = FOLLOWUP_CANCELLED_STATUS
            execution.cancelled_at = now
            execution.completed_at = now
            execution.stop_reason = "delivery_policy_blocked"
            execution.policy_snapshot_json = {**dict(execution.policy_snapshot_json or {}), "template_error": str(exc)[:300]}
            return False

    if spec.content_type == "text_audio" and template_payload:
        execution.status = FOLLOWUP_CANCELLED_STATUS
        execution.cancelled_at = now
        execution.completed_at = now
        execution.stop_reason = "template_does_not_authorize_audio"
        return False
    parts = []
    if spec.message_mode == FOLLOWUP_MESSAGE_MODE_META_TEMPLATE:
        parts.append(("text", "", None))
    elif spec.content_type != "audio" and (message_text or str(spec.message_content or "").strip()):
        parts.append(("text", message_text or str(spec.message_content or "").strip(), None))
    if spec.content_type == "audio":
        parts = [("audio", str(spec.audio_caption or "").strip(), media_payload)]
    elif spec.content_type == "text_audio":
        parts.append(("audio", str(spec.audio_caption or "").strip(), media_payload))
    execution.processing_started_at = execution.processing_started_at or now
    execution.policy_snapshot_json = {
        **dict(execution.policy_snapshot_json or {}),
        "delivery_policy": policy,
        "template_payload_present": bool(template_payload),
    }
    enviado = True
    for part_index, (part_type, part_text, part_media) in enumerate(parts, start=1):
        outbox = await enqueue_message(
            session,
            empresa_id=ctx.empresa_id,
            conexao_id=ctx.conexao_id,
            provider=ctx.provider,
            channel=ctx.channel,
            operation_type="followup_prepare_send",
            payload={
                "mode": spec.mode,
                "message_mode": spec.message_mode,
                "followup_execution_id": str(execution.id),
                "template_id": spec.template_id if part_type == "text" and template_payload else None,
                "content_type": part_type,
                "media_asset_id": spec.media_asset_id if part_type == "audio" else None,
                "part_index": part_index,
                "policy_version": FOLLOWUP_POLICY_VERSION,
            },
            idempotency_key=deterministic_idempotency_key("followup_outbox", execution.idempotency_key, part_type),
            followup_execution_id=execution.id,
        )
        await session.flush()
        execution.outbox_id = outbox.id
        logger.info(
            "FOLLOWUP_SEND_START execution_id=%s rule_id=%s empresa_id=%s outbox_id=%s part_type=%s",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
            outbox.id,
            part_type,
        )
        try:
            enviado = await dispatch_outbox_now(
                session,
                outbox=outbox,
                identificador_origem=str(contact.phone_e164 or contact.external_contact_id or ""),
                texto=part_text,
                dispatcher=despachar_mensagem,
                message_type="audio" if part_type == "audio" else "template" if template_payload else "text",
                template_payload=template_payload if part_type == "text" else None,
                media=part_media,
                send_as_voice=bool(spec.send_as_voice) if part_type == "audio" else False,
            )
            execution.external_message_id = str(outbox.external_message_id or "").strip() or None
        except Exception as exc:
            enviado = False
            execution.status = "failed"
            execution.failed_at = datetime.utcnow()
            execution.completed_at = execution.failed_at
            execution.stop_reason = str(sanitize_for_log(str(exc)))[:120] or "dispatch_failed"
            logger.warning(
                "FOLLOWUP_SEND_FAILURE execution_id=%s rule_id=%s empresa_id=%s outbox_id=%s reason=%s",
                execution.id,
                execution.followup_id,
                execution.empresa_id,
                outbox.id,
                execution.stop_reason,
            )
        if not enviado:
            break
        external_message_id = str(getattr(outbox, "external_message_id", "") or "").strip() or None
        history_text = part_text or ("Mensagem de template" if template_payload else "Audio de follow-up")
        history = MensagemHistorico(
            empresa_id=ctx.empresa_id,
            lead_id=lead.id,
            conexao_id=ctx.conexao_id,
            channel_conversation_id=conversation.id,
            channel=ctx.channel,
            provider=ctx.provider,
            external_message_id=external_message_id,
            external_conversation_id=str(getattr(conversation, "external_conversation_id", "") or "").strip() or None,
            direction="outbound",
            message_status="sent",
            provider_status="sent",
            texto=history_text,
            tipo_mensagem=part_type,
            origem_mensagem="followup",
            from_me=True,
            metadata_json={
                "origin": "followup",
                "followup_execution_id": str(execution.id),
                "followup_id": str(execution.followup_id),
                "outbox_id": str(outbox.id),
                "part_type": part_type,
                "part_index": part_index,
                "message_mode": spec.message_mode,
                "external_message_id": external_message_id,
            },
        )
        session.add(history)
        await session.flush()
        try:
            from app.services.websocket_manager import manager

            payload = {
                "id": str(history.id),
                "empresa_id": str(history.empresa_id),
                "lead_id": str(history.lead_id),
                "conexao_id": str(history.conexao_id) if history.conexao_id else None,
                "texto": history.texto or "",
                "from_me": True,
                "direction": "outbound",
                "channel": history.channel,
                "provider": history.provider,
                "external_message_id": history.external_message_id,
                "external_conversation_id": history.external_conversation_id,
                "channel_conversation_id": str(history.channel_conversation_id) if history.channel_conversation_id else None,
                "tipo_mensagem": history.tipo_mensagem,
                "origem_mensagem": history.origem_mensagem,
                "message_status": history.message_status,
                "metadata_json": dict(history.metadata_json or {}),
                "criado_em": serialize_utc_timestamp(history.criado_em),
            }
            await manager.broadcast_to_empresa(
                str(ctx.empresa_id),
                {
                    "type": "inbox.message.created",
                    "tipo_evento": "nova_mensagem_outbound",
                    "empresa_id": str(ctx.empresa_id),
                    "lead_id": str(lead.id),
                    "conversation_id": str(conversation.id),
                    "direction": "outbound",
                    "created_at": payload["criado_em"],
                    "message": payload,
                    "mensagem": payload,
                    "metadata": {"provider": ctx.provider, "channel": ctx.channel, "origin": "followup"},
                },
            )
        except Exception as exc:
            logger.debug(
                "FOLLOWUP_REALTIME_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=%s",
                execution.id,
                execution.followup_id,
                execution.empresa_id,
                sanitize_for_log(str(exc)),
            )

    if enviado:
        execution.status = "sent"
        execution.sent_at = execution.sent_at or datetime.utcnow()
        execution.completed_at = execution.completed_at or execution.sent_at
        execution.stop_reason = None
        session.add(
            LeadFollowUpLog(
                lead_id=lead.id,
                config_followup_id=execution.followup_id,
                data_envio=execution.sent_at,
                status_envio="enviado",
            )
        )
        logger.info(
            "FOLLOWUP_SEND_SUCCESS execution_id=%s rule_id=%s empresa_id=%s outbox_id=%s",
            execution.id,
            execution.followup_id,
            execution.empresa_id,
            execution.outbox_id,
        )
        return True

    execution.status = "failed"
    execution.failed_at = datetime.utcnow()
    execution.completed_at = execution.failed_at
    execution.stop_reason = execution.stop_reason or "dispatch_failed"
    logger.warning(
        "FOLLOWUP_SEND_FAILURE execution_id=%s rule_id=%s empresa_id=%s outbox_id=%s reason=%s",
        execution.id,
        execution.followup_id,
        execution.empresa_id,
        execution.outbox_id,
        execution.stop_reason,
    )
    return False


async def processar_followups_pendentes() -> dict[str, int]:
    from app.api.main import redis_client

    lock_key = "lock:followup_worker"
    lock_ttl_seconds = 45
    lock_token = str(uuid.uuid4())
    lock_acquired = False
    heartbeat_task = None

    if redis_client is not None:
        try:
            lock_acquired = bool(await redis_client.set(lock_key, lock_token, ex=lock_ttl_seconds, nx=True))
        except Exception:
            lock_acquired = True
    else:
        lock_acquired = True

    if not lock_acquired:
        return {"enviados": 0, "ignorados": 0, "erros": 0}

    async def _heartbeat_renovar_lock() -> None:
        while True:
            try:
                await asyncio.sleep(15)
                if redis_client is None:
                    continue
                valor_lock = await redis_client.get(lock_key)
                if valor_lock != lock_token:
                    return
                await redis_client.expire(lock_key, lock_ttl_seconds)
            except asyncio.CancelledError:
                return
            except Exception:
                return

    if redis_client is not None:
        heartbeat_task = asyncio.create_task(_heartbeat_renovar_lock())

    enviados = 0
    ignorados = 0
    erros = 0

    try:
        now = datetime.utcnow()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(FollowupExecution)
                .where(
                    FollowupExecution.status.in_(["scheduled", "retry"]),
                    FollowupExecution.scheduled_at <= now,
                )
                .order_by(FollowupExecution.scheduled_at.asc())
                .limit(100)
                .options(
                    selectinload(FollowupExecution.empresa),
                    selectinload(FollowupExecution.followup),
                    selectinload(FollowupExecution.lead),
                    selectinload(FollowupExecution.contact),
                    selectinload(FollowupExecution.conversation),
                    selectinload(FollowupExecution.conexao),
                )
                .with_for_update(skip_locked=True)
            )
            executions = result.scalars().all()
            logger.info("FOLLOWUP_CYCLE candidate_count=%s", len(executions))
            logger.info("FOLLOWUP_CLAIM claim_count=%s mode=for_update_skip_locked", len(executions))
            if executions:
                worker_logger.info(
                    "WORKER_JOB_CLAIM worker=followup claimed=%s claim_mode=for_update_skip_locked",
                    len(executions),
                )
            for execution in executions:
                try:
                    logger.info(
                        "FOLLOWUP_CLAIM execution_id=%s rule_id=%s empresa_id=%s scheduled_at=%s status=%s",
                        execution.id,
                        execution.followup_id,
                        execution.empresa_id,
                        execution.scheduled_at.isoformat() if execution.scheduled_at else None,
                        execution.status,
                    )
                    followup_row = (
                        await session.execute(
                            select(ConfigFollowUp, FollowupSequenceStep)
                            .join(
                                FollowupSequenceStep,
                                and_(
                                    FollowupSequenceStep.empresa_id == ConfigFollowUp.empresa_id,
                                    FollowupSequenceStep.followup_id == ConfigFollowUp.id,
                                    FollowupSequenceStep.step_order == 1,
                                ),
                                isouter=True,
                            )
                            .where(
                                ConfigFollowUp.id == execution.followup_id,
                                ConfigFollowUp.empresa_id == execution.empresa_id,
                            )
                            .limit(1)
                        )
                    ).first()
                    if not followup_row:
                        await _cancel_execution(session, execution, reason="followup_missing")
                        ignorados += 1
                        continue
                    item = followup_row[0]
                    step = followup_row[1]
                    step_data = None
                    if step is not None:
                        step_data = {
                            "id": getattr(step, "id", None),
                            "empresa_id": getattr(step, "empresa_id", None),
                            "followup_id": getattr(step, "followup_id", None),
                            "step_order": getattr(step, "step_order", None),
                            "status": getattr(step, "status", None),
                            "trigger_tag_id": getattr(step, "trigger_tag_id", None),
                            "delay_minutes": getattr(step, "delay_minutes", None),
                            "timezone": getattr(step, "timezone", None),
                            "ad_window_mode": getattr(step, "ad_window_mode", None),
                            "message_mode": getattr(step, "message_mode", None),
                            "message_free_text": getattr(step, "message_free_text", None),
                            "content_type": getattr(step, "content_type", None),
                            "media_asset_id": getattr(step, "media_asset_id", None),
                            "send_as_voice": getattr(step, "send_as_voice", None),
                            "audio_caption": getattr(step, "audio_caption", None),
                            "template_id": getattr(step, "template_id", None),
                            "variable_mapping_json": getattr(step, "variable_mapping_json", None),
                            "execute_conditions_json": getattr(step, "execute_conditions_json", None),
                            "cancel_conditions_json": getattr(step, "cancel_conditions_json", None),
                            "post_send_tag_id": getattr(step, "post_send_tag_id", None),
                            "actions_json": getattr(step, "actions_json", None),
                            "send_window_enabled": getattr(step, "send_window_enabled", None),
                            "send_window_start": getattr(step, "send_window_start", None),
                            "send_window_end": getattr(step, "send_window_end", None),
                            "limit_to_free_entry_window": getattr(step, "limit_to_free_entry_window", None),
                            "created_at": getattr(step, "created_at", None),
                            "updated_at": getattr(step, "updated_at", None),
                        }
                    spec = FollowupRuleSpec.from_row(item, step_data)
                    if not item.ativo:
                        await _cancel_execution(session, execution, reason="followup_inactive")
                        ignorados += 1
                        continue
                    if str(execution.status or "").lower() == "stopped":
                        logger.info(
                            "FOLLOWUP_SKIPPED execution_id=%s rule_id=%s empresa_id=%s reason=stopped",
                            execution.id,
                            execution.followup_id,
                            execution.empresa_id,
                        )
                        ignorados += 1
                        continue
                    if not execution.lead or not execution.contact or not execution.conversation:
                        await _cancel_execution(session, execution, reason="missing_context")
                        ignorados += 1
                        continue
                    if is_ai_blocked(execution.lead, now=now):
                        await _cancel_execution(session, execution, reason="ai_blocked")
                        ignorados += 1
                        continue

                    ok = await _dispatch_followup_execution(session, execution=execution, spec=spec)
                    if ok:
                        enviados += 1
                        worker_logger.info("WORKER_JOB_SUCCESS worker=followup outcome=sent")
                    else:
                        if str(execution.status or "").lower() == FOLLOWUP_CANCELLED_STATUS:
                            ignorados += 1
                        elif str(execution.status or "").lower() in {"scheduled", "retry"}:
                            ignorados += 1
                            worker_logger.warning("WORKER_JOB_RETRY worker=followup outcome=retry")
                        else:
                            erros += 1
                            worker_logger.error("WORKER_JOB_FAILED worker=followup outcome=terminal_failure")
                except Exception as exc:
                    retry_count = int((execution.policy_snapshot_json or {}).get("processing_retry_count", 0) or 0)
                    retry_count += 1
                    execution.policy_snapshot_json = {
                        **dict(execution.policy_snapshot_json or {}),
                        "processing_retry_count": retry_count,
                    }
                    retryable = retry_count < FOLLOWUP_MAX_PROCESSING_RETRIES
                    failure_at = datetime.utcnow()
                    if retryable:
                        execution.status = "retry"
                        execution.scheduled_at = failure_at + timedelta(
                            seconds=FOLLOWUP_RETRY_BACKOFF_SECONDS * (2 ** (retry_count - 1))
                        )
                        execution.stop_reason = "processing_retry"
                        logger.warning(
                            "FOLLOWUP_RETRY execution_id=%s rule_id=%s empresa_id=%s attempt=%s next_attempt_at=%s reason=%s",
                            getattr(execution, "id", None),
                            getattr(execution, "followup_id", None),
                            getattr(execution, "empresa_id", None),
                            retry_count,
                            execution.scheduled_at.isoformat(),
                            sanitize_for_log(str(exc)),
                        )
                        worker_logger.warning(
                            "WORKER_JOB_RETRY worker=followup attempt=%s exception_type=%s",
                            retry_count,
                            type(exc).__name__,
                        )
                    else:
                        execution.status = "failed"
                        execution.failed_at = failure_at
                        execution.completed_at = failure_at
                        execution.stop_reason = "processing_retry_exhausted"
                        logger.error(
                            "FOLLOWUP_FAILED execution_id=%s rule_id=%s empresa_id=%s attempts=%s reason=%s",
                            getattr(execution, "id", None),
                            getattr(execution, "followup_id", None),
                            getattr(execution, "empresa_id", None),
                            retry_count,
                            sanitize_for_log(str(exc)),
                        )
                        worker_logger.error(
                            "WORKER_JOB_FAILED worker=followup attempts=%s exception_type=%s",
                            retry_count,
                            type(exc).__name__,
                        )
                    logger.exception(
                        "FOLLOWUP_SEND_FAILURE execution_id=%s rule_id=%s empresa_id=%s reason=%s",
                        getattr(execution, "id", None),
                        getattr(execution, "followup_id", None),
                        getattr(execution, "empresa_id", None),
                        sanitize_for_log(str(exc)),
                    )
                    erros += 1
                    continue

            await session.commit()

        if redis_client is not None:
            try:
                hoje = datetime.utcnow().strftime("%Y-%m-%d")
                key_leads_hoje = f"metrics:followup:leads_processados:{hoje}"
                key_erros_recentes = "metrics:followup:erros_recentes"
                key_ultima_execucao = "metrics:followup:ultima_execucao"
                if enviados > 0:
                    await redis_client.incrby(key_leads_hoje, int(enviados))
                    await redis_client.expire(key_leads_hoje, 60 * 60 * 24 * 3)
                await redis_client.set(key_erros_recentes, int(erros), ex=60 * 60 * 24)
                await redis_client.set(key_ultima_execucao, datetime.utcnow().isoformat(), ex=60 * 60 * 24 * 3)
            except Exception:
                pass

        return {"enviados": enviados, "ignorados": ignorados, "erros": erros}
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if redis_client is not None:
            try:
                valor_lock = await redis_client.get(lock_key)
                if valor_lock == lock_token:
                    await redis_client.delete(lock_key)
            except Exception:
                pass
