import base64
import logging
import re
import traceback

import httpx
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from typing import Dict, Any, List
from pydantic import BaseModel, Field
import uuid
from datetime import datetime, timedelta

from db.database import AsyncSessionLocal, get_db
from db.models import CampaignRecipient, ChannelContact, ChannelConversation, CRMLead, Empresa, MensagemHistorico, Conexao, TagCRM
from sqlalchemy import select, func, cast, String, desc, or_, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.schemas import ConversaListaResponse
from app.services.evolution_service import _obter_credenciais_conexao_ou_empresa
from app.services.mensageria.dispatcher import dispatch_outbound_message
from app.services.mensageria.schemas import StandardOutgoingMessage
from app.services.channel_factory import despachar_mensagem
from app.services.rosangela_ai_control import get_conversation_manual_ai_state, resolve_conversation_ai_state
from app.services.whatsapp_address import normalize_whatsapp_address
from app.services.websocket_manager import manager
from app.services.meta_attribution import dados_adicionais_for_public_response
from app.api.routers.auth import require_tenant_access
from app.api.time_serialization import serialize_utc_timestamp
from app.services.commercial_journey import commercial_journey, enabled as commercial_journey_enabled

router = APIRouter(
    prefix="/api/empresas",
    tags=["Inbox Live Chat"],
    # All Inbox routes carry {empresa_id}; this router policy closes gaps for
    # endpoints that previously omitted the per-handler dependency.
    dependencies=[Depends(require_tenant_access)],
)
logger = logging.getLogger(__name__)

SIMULADOR_LEAD_ID = "ID_TESTE_SIMULADOR"
TECHNICAL_CHAT_EVENT_KINDS = ("status", "reaction")


@router.get("/{empresa_id}/inbox/leads/{lead_id}/journey")
async def get_commercial_journey(
    empresa_id: uuid.UUID,
    lead_id: uuid.UUID,
    category: str | None = Query(default=None, pattern="^(acquisition|conversation|message|crm|commercial|followup|campaign|checkout|payment|booking)$"),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(require_tenant_access),
):
    if not commercial_journey_enabled():
        raise HTTPException(status_code=404, detail="Jornada comercial indisponível.")
    try:
        result = await commercial_journey(db, empresa_id=empresa_id, lead_id=lead_id,
                                          category=category, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Filtro ou cursor da jornada inválido.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return result


def _telefone_eh_simulador(telefone: str | None) -> bool:
    return str(telefone or "") == SIMULADOR_LEAD_ID


def _visible_chat_message_filter():
    event_kind = func.lower(func.coalesce(MensagemHistorico.metadata_json["event_kind"].astext, ""))
    return event_kind.notin_(TECHNICAL_CHAT_EVENT_KINDS)


def _reaction_index(messages: list[MensagemHistorico]) -> dict[str, list[dict[str, str | None]]]:
    reactions: dict[str, list[dict[str, str | None]]] = {}
    for message in messages:
        if str(getattr(message, "tipo_mensagem", "") or "").lower() != "reaction":
            continue
        metadata = getattr(message, "metadata_json", None) or {}
        reaction = metadata.get("reaction") if isinstance(metadata, dict) else None
        if not isinstance(reaction, dict):
            continue
        target_id = str(reaction.get("message_id") or "").strip()
        emoji = reaction.get("emoji")
        if not target_id or emoji in (None, ""):
            continue
        reactions.setdefault(target_id, []).append(
            {
                "emoji": str(emoji),
                "message_id": str(getattr(message, "external_message_id", "") or "") or None,
                "direction": str(getattr(message, "direction", "") or "") or None,
            }
        )
    return reactions

class SendMessagePayload(BaseModel):
    texto: str
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


def _inferir_tipo_mensagem(content_type: str | None) -> str:
    ct = str(content_type or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("video/"):
        return "video"
    return "document"


def _normalizar_numero_whatsapp(telefone: str | None) -> str:
    valor = str(telefone or "").strip()
    if "@s.whatsapp.net" in valor:
        valor = valor.replace("@s.whatsapp.net", "")
    return re.sub(r"\D", "", valor)


def _formatar_jid_whatsapp(telefone: str | None) -> str:
    numero = _normalizar_numero_whatsapp(telefone)
    return f"{numero}@s.whatsapp.net" if numero else ""


def _extrair_foto_url_resposta(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    candidatos = [
        payload.get("profilePictureUrl"),
        payload.get("profilePicUrl"),
        payload.get("pictureUrl"),
        payload.get("photoUrl"),
        payload.get("url"),
    ]

    for chave in ("data", "response", "result"):
        nested = payload.get(chave)
        if isinstance(nested, dict):
            candidatos.extend(
                [
                    nested.get("profilePictureUrl"),
                    nested.get("profilePicUrl"),
                    nested.get("pictureUrl"),
                    nested.get("photoUrl"),
                    nested.get("url"),
                ]
            )

    for candidato in candidatos:
        if isinstance(candidato, str) and candidato.strip():
            return candidato.strip()

    return None


async def _buscar_conexao_evolution_ativa(session: AsyncSession, empresa_uuid: uuid.UUID) -> Conexao | None:
    tipos_aceitos = ["evolution", "EVOLUTION"]
    status_aceitos = ["ativo", "connected", "conectado", "open"]
    result = await session.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            cast(Conexao.tipo, String).in_(tipos_aceitos),
            func.lower(Conexao.status).in_(status_aceitos),
        )
    )
    return result.scalars().first()


def _provider_canonico(provider: str | None) -> str:
    raw = str(provider or "").strip().lower()
    if raw in {"meta", "meta_official", "whatsapp_meta_official"}:
        return "meta_whatsapp"
    if raw in {"evolution", "evolution_legacy", "whatsapp_evolution"}:
        return "evolution"
    return raw


def _provider_conexao_canonico(conexao: Conexao) -> str:
    provider = _provider_canonico(getattr(conexao, "provider", None))
    if provider:
        return provider
    tipo = str(getattr(getattr(conexao, "tipo", ""), "value", getattr(conexao, "tipo", "")) or "").strip().lower()
    if tipo in {"meta", "meta_whatsapp"}:
        return "meta_whatsapp"
    if "evolution" in tipo:
        return "evolution"
    return tipo


def _recipient_from_conversation_contact(contact: ChannelContact, channel: str) -> str:
    """Resolve only the address persisted on the selected conversation contact."""
    channel = str(channel or "").strip().lower()
    external_id = str(getattr(contact, "external_contact_id", "") or "").strip()
    if not external_id:
        raise HTTPException(status_code=409, detail="Destinatário da conversa indisponível")

    if channel == "whatsapp":
        primary = normalize_whatsapp_address(external_id, default_country=None)
        if not primary.valid or not primary.digits:
            raise HTTPException(status_code=409, detail="Endereço WhatsApp da conversa inválido")
        # external_contact_id is the identity attached to this conversation.
        # phone_e164 is a second canonical field and, when present, must agree.
        # normalized_address is a legacy/display projection and may be stale;
        # it is never used to choose a different destination.
        stored_phone = getattr(contact, "phone_e164", None)
        if stored_phone not in (None, ""):
            candidate = normalize_whatsapp_address(str(stored_phone), default_country=None)
            if not candidate.valid or candidate.digits != primary.digits:
                raise HTTPException(status_code=409, detail="Endereço da conversa inconsistente")
        return primary.digits

    if channel == "instagram":
        return external_id

    raise HTTPException(status_code=400, detail="Canal da conversa não suportado para envio manual")


async def _resolve_inbox_send_context(
    db: AsyncSession,
    *,
    empresa_id: str | uuid.UUID,
    lead_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
) -> dict[str, Any]:
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
        lead_uuid = uuid.UUID(str(lead_id))
        conversation_uuid = uuid.UUID(str(conversation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Identidade da conversa inválida")

    result_lead = await db.execute(
        select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.id == lead_uuid)
    )
    lead = result_lead.scalars().first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")

    result_context = await db.execute(
        select(ChannelConversation, ChannelContact, Conexao)
        .join(
            ChannelContact,
            and_(
                ChannelContact.id == ChannelConversation.contact_id,
                ChannelContact.empresa_id == ChannelConversation.empresa_id,
            ),
        )
        .join(
            Conexao,
            and_(
                Conexao.id == ChannelConversation.conexao_id,
                Conexao.empresa_id == ChannelConversation.empresa_id,
            ),
        )
        .where(
            ChannelConversation.empresa_id == empresa_uuid,
            ChannelConversation.id == conversation_uuid,
            ChannelConversation.lead_id == lead_uuid,
            ChannelContact.empresa_id == empresa_uuid,
            Conexao.empresa_id == empresa_uuid,
        )
        .limit(2)
    )
    row = result_context.first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversa não encontrada para este lead")
    conversation, contact, conexao = row[0], row[1], row[2]

    if getattr(contact, "lead_id", None) not in (None, lead_uuid):
        raise HTTPException(status_code=409, detail="Associação do contato com a conversa inconsistente")

    channel = str(getattr(conversation, "channel", "") or "").strip().lower()
    contact_channel = str(getattr(contact, "channel", "") or "").strip().lower()
    connection_channel = str(getattr(conexao, "channel", "") or "").strip().lower()
    conversation_provider = _provider_canonico(getattr(conversation, "provider", None))
    contact_provider = _provider_canonico(getattr(contact, "provider", None))
    connection_provider = _provider_conexao_canonico(conexao)
    if (
        not channel
        or channel != contact_channel
        or (connection_channel and connection_channel != channel)
        or not conversation_provider
        or conversation_provider != contact_provider
        or conversation_provider != connection_provider
        or str(getattr(conversation, "conexao_id", "")) != str(getattr(conexao, "id", ""))
        or str(getattr(contact, "conexao_id", "")) != str(getattr(conexao, "id", ""))
    ):
        raise HTTPException(status_code=409, detail="Canal ou conexão da conversa inconsistente")

    destination = _recipient_from_conversation_contact(contact, channel)
    if channel == "instagram":
        contact_metadata = getattr(contact, "metadata_json", None) or {}
        identity_type = str(contact_metadata.get("identity_type") or "").strip()
        persisted_lead_igsid = str((lead.dados_adicionais or {}).get("instagram_igsid") or "").strip()
        if identity_type != "IGSID" and persisted_lead_igsid != destination:
            raise HTTPException(
                status_code=409,
                detail="IGSID indisponível para esta conversa Direct",
            )
    return {
        "empresa_id": empresa_uuid,
        "lead": lead,
        "conversation": conversation,
        "contact": contact,
        "conexao": conexao,
        "provider": conversation_provider,
        "channel": channel,
        "destination": destination,
    }


async def _resolve_legacy_send_context(
    db: AsyncSession,
    *,
    empresa_id: str | uuid.UUID,
    telefone: str,
) -> dict[str, Any]:
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="ID da empresa inválido")

    result_lead = await db.execute(
        select(CRMLead).where(
            CRMLead.empresa_id == empresa_uuid,
            CRMLead.telefone_contato == telefone,
        )
    )
    lead = result_lead.scalars().first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")

    result_conversations = await db.execute(
        select(ChannelConversation).where(
            ChannelConversation.empresa_id == empresa_uuid,
            ChannelConversation.lead_id == lead.id,
        ).limit(2)
    )
    conversations = result_conversations.scalars().all()
    if len(conversations) != 1:
        raise HTTPException(status_code=409, detail="Selecione uma conversa vinculada antes de enviar")
    return await _resolve_inbox_send_context(
        db,
        empresa_id=empresa_uuid,
        lead_id=lead.id,
        conversation_id=conversations[0].id,
    )


async def _lock_and_find_duplicate_inbox_message(
    db: AsyncSession,
    *,
    context: dict[str, Any],
    client_message_id: str | None,
) -> MensagemHistorico | None:
    key = str(client_message_id or "").strip()
    if not key:
        return None
    lock_scope = ":".join((
        "inbox-send",
        str(context["empresa_id"]),
        str(context["lead"].id),
        str(context["conversation"].id),
        key,
    ))
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_scope, 0))"), {"lock_scope": lock_scope})
    result = await db.execute(
        select(MensagemHistorico)
        .where(
            MensagemHistorico.empresa_id == context["empresa_id"],
            MensagemHistorico.lead_id == context["lead"].id,
            MensagemHistorico.channel_conversation_id == context["conversation"].id,
            MensagemHistorico.metadata_json["inbox_client_message_id"].astext == key,
        )
        .order_by(desc(MensagemHistorico.criado_em), desc(MensagemHistorico.id))
        .limit(1)
    )
    return result.scalars().first()


def _serialize_manual_outbound_message(message: MensagemHistorico) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "texto": str(message.texto or ""),
        "from_me": bool(message.from_me),
        "tipo_mensagem": str(message.tipo_mensagem or "text"),
        "origem_mensagem": message.origem_mensagem,
        "media_url": str(message.media_url) if message.media_url else None,
        "criado_em": serialize_utc_timestamp(message.criado_em) or "",
        "status": str(message.message_status or "sent"),
    }


AI_STATE_MESSAGES = {
    "allowed": (
        "IA autorizada",
        "A IA está pronta para processar novas mensagens.",
    ),
    "ai_manual_disabled": (
        "IA desligada nesta conversa",
        "Ative o controle de IA para permitir respostas automáticas.",
    ),
    "tenant_llm_provider_error": (
        "Falha ao consultar modelo",
        "A resposta automatica nao foi enviada; a conversa foi encaminhada ao humano.",
    ),
    "tenant_llm_unavailable": (
        "IA indisponivel",
        "A configuracao do modelo nao permitiu gerar resposta automatica.",
    ),
    "tenant_llm_empty_response": (
        "Modelo sem resposta",
        "O modelo retornou vazio e nada foi enviado ao cliente.",
    ),
    "tenant_tool_planner_error": (
        "Falha ao iniciar IA",
        "A IA nao conseguiu preparar o contexto da resposta.",
    ),
    "fiscal_validation_failed": (
        "Resposta bloqueada",
        "A resposta automatica foi bloqueada pela validacao operacional.",
    ),
    "response_guard_blocked": (
        "Resposta bloqueada",
        "A resposta automatica foi bloqueada pelos guardas de seguranca.",
    ),
}


def _ai_state_message(reason: str | None, fallback: str | None = None) -> tuple[str, str]:
    value = str(reason or "").strip()
    if value in AI_STATE_MESSAGES:
        return AI_STATE_MESSAGES[value]
    label = str(fallback or value or "Estado da IA").strip()
    return label, label


async def _buscar_conversa_principal_lead(session: AsyncSession, empresa_uuid: uuid.UUID, lead_id: uuid.UUID) -> ChannelConversation | None:
    result = await session.execute(
        select(ChannelConversation)
        .where(
            ChannelConversation.empresa_id == empresa_uuid,
            ChannelConversation.lead_id == lead_id,
        )
        .order_by(ChannelConversation.atualizado_em.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _buscar_conexao_historico_lead(
    session: AsyncSession,
    empresa_uuid: uuid.UUID,
    lead_id: uuid.UUID,
) -> Conexao | None:
    """Resolve the active WhatsApp connection actually used by this lead's history."""
    status_aceitos = ["ativo", "connected", "conectado", "open"]
    result = await session.execute(
        select(Conexao)
        .join(MensagemHistorico, MensagemHistorico.conexao_id == Conexao.id)
        .where(
            MensagemHistorico.empresa_id == empresa_uuid,
            MensagemHistorico.lead_id == lead_id,
            Conexao.empresa_id == empresa_uuid,
            func.lower(Conexao.channel) == "whatsapp",
            func.lower(Conexao.status).in_(status_aceitos),
        )
        .order_by(MensagemHistorico.criado_em.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _carregar_tags_empresa(session: AsyncSession, empresa_uuid: uuid.UUID) -> dict[str, TagCRM]:
    result = await session.execute(
        select(TagCRM).where(TagCRM.empresa_id == empresa_uuid)
    )
    tags = result.scalars().all()
    return {str(tag.id): tag for tag in tags}


def _montar_tags_frontend(tags_lead: list[str] | None, tags_por_id: dict[str, TagCRM]) -> list[dict[str, Any]]:
    retorno: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for item in (tags_lead or []):
        tag_id = str(item or "").strip()
        if not tag_id or tag_id in vistos:
            continue
        tag = tags_por_id.get(tag_id)
        if not tag:
            continue
        vistos.add(tag_id)
        retorno.append(
            {
                "id": str(tag.id),
                "nome": str(tag.nome or ""),
                "cor": str(tag.cor or "#2563eb"),
            }
        )
    return retorno

@router.get("/{empresa_id}/inbox", response_model=List[ConversaListaResponse])
async def listar_inbox(empresa_id: str, _=Depends(require_tenant_access)):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            tags_por_id = await _carregar_tags_empresa(session, empresa_uuid)
            latest_at = (
                select(MensagemHistorico.criado_em)
                .where(MensagemHistorico.empresa_id == empresa_uuid, MensagemHistorico.lead_id == CRMLead.id)
                .order_by(desc(MensagemHistorico.criado_em), desc(MensagemHistorico.id))
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            latest_text = (
                select(MensagemHistorico.texto)
                .where(MensagemHistorico.empresa_id == empresa_uuid, MensagemHistorico.lead_id == CRMLead.id)
                .order_by(desc(MensagemHistorico.criado_em), desc(MensagemHistorico.id))
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            latest_conversation_id = (
                select(ChannelConversation.id)
                .where(
                    ChannelConversation.empresa_id == empresa_uuid,
                    ChannelConversation.lead_id == CRMLead.id,
                )
                .order_by(
                    desc(ChannelConversation.last_message_at).nullslast(),
                    desc(ChannelConversation.atualizado_em),
                    desc(ChannelConversation.id),
                )
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            # Campaign history can be linked to a canonical conversation before
            # an older lead record has its lead_id backfilled. Keep the Inbox
            # identity sourced from persisted history, never from a phone-only
            # synthetic object.
            latest_history_conversation_id = (
                select(MensagemHistorico.channel_conversation_id)
                .where(
                    MensagemHistorico.empresa_id == empresa_uuid,
                    MensagemHistorico.lead_id == CRMLead.id,
                    MensagemHistorico.channel_conversation_id.is_not(None),
                )
                .order_by(desc(MensagemHistorico.criado_em), desc(MensagemHistorico.id))
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            latest_conversation_id = func.coalesce(latest_conversation_id, latest_history_conversation_id)
            latest_conversation_metadata = (
                select(ChannelConversation.metadata_json)
                .where(
                    ChannelConversation.empresa_id == empresa_uuid,
                    ChannelConversation.lead_id == CRMLead.id,
                )
                .order_by(
                    desc(ChannelConversation.last_message_at).nullslast(),
                    desc(ChannelConversation.atualizado_em),
                    desc(ChannelConversation.id),
                )
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            latest_contact_display_name = (
                select(ChannelContact.display_name)
                .join(ChannelConversation, ChannelConversation.contact_id == ChannelContact.id)
                .where(
                    ChannelConversation.empresa_id == CRMLead.empresa_id,
                    ChannelConversation.lead_id == CRMLead.id,
                )
                .order_by(desc(ChannelConversation.last_message_at).nullslast(), desc(ChannelConversation.atualizado_em))
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            campaign_without_conversation = (
                select(CampaignRecipient.id)
                .where(
                    CampaignRecipient.empresa_id == empresa_uuid,
                    CampaignRecipient.lead_id == CRMLead.id,
                    CampaignRecipient.conversation_id.is_(None),
                    CampaignRecipient.status.in_(("sent", "delivered", "read")),
                )
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            latest_outbound_at = (
                select(MensagemHistorico.criado_em)
                .where(
                    MensagemHistorico.empresa_id == empresa_uuid,
                    MensagemHistorico.lead_id == CRMLead.id,
                    or_(MensagemHistorico.from_me.is_(True), MensagemHistorico.direction == "outbound"),
                )
                .order_by(desc(MensagemHistorico.criado_em), desc(MensagemHistorico.id))
                .limit(1).correlate(CRMLead).scalar_subquery()
            )
            unread_count = (
                select(func.count(MensagemHistorico.id))
                .where(
                    MensagemHistorico.empresa_id == empresa_uuid,
                    MensagemHistorico.lead_id == CRMLead.id,
                    or_(MensagemHistorico.from_me.is_(False), MensagemHistorico.direction == "inbound"),
                    MensagemHistorico.criado_em > func.coalesce(latest_outbound_at, CRMLead.criado_em),
                )
                .correlate(CRMLead).scalar_subquery()
            )
            result = await session.execute(
                select(
                    CRMLead,
                    latest_at,
                    latest_text,
                    unread_count,
                    latest_conversation_id,
                    latest_conversation_metadata,
                    latest_contact_display_name,
                    campaign_without_conversation,
                )
                .where(CRMLead.empresa_id == empresa_uuid)
                .options(selectinload(CRMLead.etapa))
                .order_by(desc(latest_at).nullslast(), desc(CRMLead.criado_em), desc(CRMLead.id))
            )
            rows = result.all()
            
            leads_retorno = []
            now = datetime.utcnow()
            for l, atividade_at, atividade_texto, nao_lidas, conversation_id, conversation_metadata, contact_display_name, campaign_without_conversation_id in rows:
                bot_pausado = False
                if l.bot_pausado_ate and l.bot_pausado_ate > now:
                    bot_pausado = True
                
                conversation_metadata = conversation_metadata if isinstance(conversation_metadata, dict) else {}
                is_campaign_created = conversation_metadata.get("campaign_created_new_conversation") is True
                trusted_name_source = str(conversation_metadata.get("trusted_display_name_source") or "").strip().lower()
                chat_name = l.nome_contato
                if is_campaign_created and trusted_name_source == "meta_profile" and contact_display_name:
                    chat_name = contact_display_name
                elif (is_campaign_created and trusted_name_source not in {"meta_profile", "manual"}) or (
                    not conversation_id and campaign_without_conversation_id
                ):
                    chat_name = l.telefone_contato
                leads_retorno.append({
                    "id": str(l.id),
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "nome_contato": chat_name,
                    "foto_url": l.foto_url,
                    "telefone_contato": l.telefone_contato,
                    "ultima_mensagem": (str(atividade_texto)[:240] if atividade_texto else l.historico_resumo or None),
                    "ultima_mensagem_data": serialize_utc_timestamp(atividade_at),
                    "nao_lidas": int(nao_lidas or 0),
                    "status_atendimento": str(l.status_atendimento or "aberto"),
                    "bot_pausado": bot_pausado,
                    "bot_pausado_ate": serialize_utc_timestamp(l.bot_pausado_ate),
                    "ia_ativa": bool(getattr(l, "ia_ativa", True)),
                    "etapa_crm": l.etapa.nome if l.etapa else None,
                    "tags": _montar_tags_frontend(l.tags, tags_por_id),
                    "historico_resumo": l.historico_resumo or "",
                    "dados_adicionais": dados_adicionais_for_public_response(l.dados_adicionais),
                })
            
            return leads_retorno
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{empresa_id}/inbox/conversas")
async def listar_inbox_conversas_multicanal(empresa_id: str, _=Depends(require_tenant_access)):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="ID da empresa inválido")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChannelConversation, CRMLead, ChannelContact)
            .outerjoin(CRMLead, CRMLead.id == ChannelConversation.lead_id)
            .outerjoin(ChannelContact, ChannelContact.id == ChannelConversation.contact_id)
            .where(ChannelConversation.empresa_id == empresa_uuid)
            .order_by(ChannelConversation.last_message_at.desc().nullslast(), ChannelConversation.criado_em.desc())
            .limit(200)
        )
        rows = []
        for conversation, lead, contact in result.all():
            conversation_metadata = conversation.metadata_json if isinstance(conversation.metadata_json, dict) else {}
            name_source = str(conversation_metadata.get("trusted_display_name_source") or "").strip().lower()
            if name_source in {"manual", "meta_profile"} and contact and contact.display_name:
                display_name = str(contact.display_name).strip()
            elif conversation_metadata.get("campaign_created_new_conversation") is True:
                display_name = str(
                    (getattr(contact, "phone_e164", None) or getattr(contact, "normalized_address", None)
                     or getattr(lead, "telefone_contato", None) or "")
                ).strip()
            else:
                display_name = str(getattr(lead, "nome_contato", "") or "").strip() if lead else None
            rows.append(
                {
                    "conversation_id": str(conversation.id),
                    "empresa_id": str(conversation.empresa_id),
                    "conexao_id": str(conversation.conexao_id),
                    "contact_id": str(conversation.contact_id),
                    "lead_id": str(conversation.lead_id) if conversation.lead_id else None,
                    "lead_nome": display_name or None,
                    "channel": conversation.channel,
                    "provider": conversation.provider,
                    "conversation_key": conversation.conversation_key,
                    "status": conversation.status,
                    "last_message_at": serialize_utc_timestamp(conversation.last_message_at),
                    "last_inbound_at": serialize_utc_timestamp(conversation.last_inbound_at),
                    "last_outbound_at": serialize_utc_timestamp(conversation.last_outbound_at),
                }
            )
        return rows


@router.get("/{empresa_id}/inbox/conversas/{conversation_id}/mensagens")
async def listar_mensagens_conversa_multicanal(
    empresa_id: str,
    conversation_id: str,
    _=Depends(require_tenant_access),
):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conversation_uuid = uuid.UUID(conversation_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Identificador inválido")

    async with AsyncSessionLocal() as session:
        result_conversation = await session.execute(
            select(ChannelConversation).where(
                ChannelConversation.empresa_id == empresa_uuid,
                ChannelConversation.id == conversation_uuid,
            )
        )
        conversation = result_conversation.scalars().first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversa não encontrada")
        result = await session.execute(
            select(MensagemHistorico)
            .where(
                MensagemHistorico.empresa_id == empresa_uuid,
                MensagemHistorico.channel_conversation_id == conversation_uuid,
            )
            .order_by(MensagemHistorico.criado_em.asc(), MensagemHistorico.id.asc())
        )
        all_messages = list(result.scalars().all())
        reaction_index = _reaction_index(all_messages)
        return [
            {
                "id": str(msg.id),
                "conversation_id": str(conversation_uuid),
                "conexao_id": str(msg.conexao_id) if msg.conexao_id else None,
                "channel": msg.channel,
                "provider": msg.provider,
                "external_message_id": msg.external_message_id,
                "direction": msg.direction,
                "message_status": msg.message_status,
                "provider_status": msg.provider_status,
                "tipo_mensagem": msg.tipo_mensagem,
                "origem_mensagem": msg.origem_mensagem,
                "from_me": bool(msg.from_me),
                "metadata_json": msg.metadata_json or {},
                "reactions": reaction_index.get(str(msg.external_message_id or ""), []),
                "criado_em": serialize_utc_timestamp(msg.criado_em),
            }
            for msg in all_messages
            if str(getattr(msg, "tipo_mensagem", "") or "").lower() not in TECHNICAL_CHAT_EVENT_KINDS
            and str((msg.metadata_json or {}).get("event_kind") or "").lower() not in TECHNICAL_CHAT_EVENT_KINDS
        ]

@router.get("/{empresa_id}/inbox/{telefone}")
async def listar_historico_lead(empresa_id: str, telefone: str, _=Depends(require_tenant_access)):
    print(">>> DEBUG: Rota de histórico acessada com sucesso! <<<")
    if _telefone_eh_simulador(telefone):
        return []

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        return []

    try:
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return []

            result_msgs = await session.execute(
                select(MensagemHistorico)
                .where(
                    MensagemHistorico.lead_id == lead.id,
                )
                .order_by(MensagemHistorico.criado_em.asc(), MensagemHistorico.id.asc())
            )
            mensagens = result_msgs.scalars().all()
            if not mensagens:
                return []

            print(f"DEBUG: Encontradas {len(mensagens)} mensagens para o lead {lead.id}")

            reaction_index = _reaction_index(list(mensagens))
            output = []
            for m in mensagens:
                if (
                    str(getattr(m, "tipo_mensagem", "") or "").lower() in TECHNICAL_CHAT_EVENT_KINDS
                    or str((getattr(m, "metadata_json", None) or {}).get("event_kind") or "").lower()
                    in TECHNICAL_CHAT_EVENT_KINDS
                ):
                    continue
                try:
                    criado_em_value = getattr(m, "criado_em", None)
                    output.append({
                        "id": str(getattr(m, "id", "erro-id")),
                        "texto": str(getattr(m, "texto", "[Sem texto]")),
                        "origem_mensagem": getattr(m, "origem_mensagem", None),
                        "from_me": bool(getattr(m, "from_me", False)),
                        "tipo_mensagem": str(getattr(m, "tipo_mensagem", "text")),
                        "media_url": str(m.media_url) if getattr(m, "media_url", None) else None,
                        "metadata_json": getattr(m, "metadata_json", None) or {},
                        "reactions": reaction_index.get(str(getattr(m, "external_message_id", "") or ""), []),
                        "criado_em": serialize_utc_timestamp(criado_em_value) if hasattr(criado_em_value, "isoformat") else str(criado_em_value or ""),
                    })
                except Exception as e:
                    print(f"FALHA NO ITEM: {getattr(m, 'id', 'sem-id')}")
                    print(f"ERRO: {e}")
            return output
    except Exception as e:
        print(f"ERRO GERAL NA ROTA DE HISTÓRICO: {e}")
        return []


@router.get("/{empresa_id}/inbox/{telefone}/foto")
async def obter_foto_lead(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"foto_url": None}

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        return {"foto_url": None}

    try:
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")

            now = datetime.utcnow()
            # Se a foto foi atualizada nas últimas 24h, retorna do banco (seja a URL ou None)
            if lead.foto_atualizada_em and (now - lead.foto_atualizada_em) < timedelta(hours=24):
                return {"foto_url": lead.foto_url}

            conexao = await _buscar_conexao_evolution_ativa(session, empresa_uuid)
            if not conexao:
                return {"foto_url": lead.foto_url or None}

            credenciais = await _obter_credenciais_conexao_ou_empresa(session, conexao)
            if not credenciais:
                return {"foto_url": lead.foto_url or None}

            evolution_url = str(credenciais.get("evolution_url") or "").strip().rstrip("/")
            evolution_apikey = str(credenciais.get("evolution_apikey") or "").strip()
            instance_name = str(credenciais.get("evolution_instance") or "").strip()
            numero = _normalizar_numero_whatsapp(telefone)
            numero_jid = _formatar_jid_whatsapp(telefone)

            if not all([evolution_url, evolution_apikey, instance_name, numero]):
                return {"foto_url": lead.foto_url or None}

            foto_url = None
            headers = {"apikey": evolution_apikey, "Content-Type": "application/json"}
            endpoint_get = f"{evolution_url}/profile/fetchProfile/{instance_name}"

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                    response_get = await client.get(
                        endpoint_get,
                        headers=headers,
                        params={"number": numero},
                    )
                    if response_get.status_code in (200, 201):
                        try:
                            foto_url = _extrair_foto_url_resposta(response_get.json())
                        except Exception:
                            foto_url = None

            except Exception as exc:
                print(f"[Inbox Foto] Falha ao buscar foto na Evolution: {exc}")
                return {"foto_url": lead.foto_url or None}

            # Profile photo lookups are read-only. Persist refreshes through an
            # explicit background/job path instead of mutating a lead on GET.
            return {"foto_url": foto_url or lead.foto_url or None}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Inbox Foto] Erro ao obter foto do lead: {e}")
        return {"foto_url": None}


@router.get("/{empresa_id}/inbox/{telefone}/ai-state")
async def consultar_estado_efetivo_ia(
    empresa_id: str,
    telefone: str,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_tenant_access),
):
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="ID da empresa inválido")

    result_empresa = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    empresa = result_empresa.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    result_lead = await db.execute(
        select(CRMLead).where(
            CRMLead.empresa_id == empresa_uuid,
            CRMLead.telefone_contato == telefone,
        )
    )
    lead = result_lead.scalars().first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")

    conversation = await _buscar_conversa_principal_lead(db, empresa_uuid, lead.id)
    provider = _provider_canonico(getattr(conversation, "provider", None) if conversation else None) or "whatsapp"
    state = await resolve_conversation_ai_state(
        db,
        empresa=empresa,
        lead=lead,
        conversation=conversation,
        connection_id=str(getattr(conversation, "conexao_id", "") or ""),
        provider=provider,
        phone=telefone,
        persist=False,
    )
    manual_state = get_conversation_manual_ai_state(lead)
    logger.info(
        "ai.manual_state.read empresa_id=%s conversation_id=%s lead_id=%s enabled=%s reason_code=%s provider=%s",
        empresa_id,
        str(getattr(conversation, "id", "") or ""),
        str(lead.id),
        manual_state.manual_enabled,
        state.reason,
        provider,
    )
    effective_state = "authorized" if state.state == "active" else "blocked"
    label, description = _ai_state_message(state.reason, state.reason_label)
    if state.reason == "human_takeover":
        effective_state = "human_takeover"
    elif state.reason == "ai_manual_disabled":
        effective_state = "manual_paused"
    elif state.reason == "campaign_ai_disabled":
        label, description = "IA desativada pela campanha", "A campanha não autoriza resposta automática da IA."

    return {
        "manual_enabled": manual_state.manual_enabled,
        "manual_source": manual_state.source,
        "manual_updated_at": manual_state.as_dict().get("updated_at"),
        "effective_state": effective_state,
        "reason_code": state.reason,
        "label": label,
        "description": description,
        "phone_match_state": state.phone_match_state,
        "human_takeover": state.human_state in {"human_takeover", "bot_paused"},
        "provider": "meta_whatsapp" if provider in {"meta_whatsapp", "meta", "meta_official"} else provider,
        "channel": getattr(conversation, "channel", None) or "whatsapp",
        "evaluated_at": datetime.utcnow().isoformat(),
    }

@router.post("/{empresa_id}/inbox/{telefone}/send")
@router.post("/{empresa_id}/inbox/{telefone}/mensagens")
async def enviar_mensagem(
    empresa_id: str,
    telefone: str,
    payload: SendMessagePayload,
    db: AsyncSession = Depends(get_db),
):
    if _telefone_eh_simulador(telefone):
        return {"status": "success"}
    try:
        context = await _resolve_legacy_send_context(db, empresa_id=empresa_id, telefone=telefone)
        return await _dispatch_manual_text_message(db, context=context, payload=payload)
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERRO REAL DE ENVIO: {str(e)}")
        traceback.print_exc()
        print(f"[Inbox] Erro ao enviar mensagem manualmente: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{empresa_id}/inbox/leads/{lead_id}/conversations/{conversation_id}/send")
async def enviar_mensagem_por_conversa(
    empresa_id: str,
    lead_id: str,
    conversation_id: str,
    payload: SendMessagePayload,
    db: AsyncSession = Depends(get_db),
):
    try:
        context = await _resolve_inbox_send_context(
            db,
            empresa_id=empresa_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
        )
        return await _dispatch_manual_text_message(
            db, context=context, payload=payload, require_client_message_id=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Inbox manual text send failed")
        raise HTTPException(status_code=500, detail="Não foi possível enviar a mensagem") from e


async def _dispatch_manual_text_message(
    db: AsyncSession,
    *,
    context: dict[str, Any],
    payload: SendMessagePayload,
    require_client_message_id: bool = False,
) -> dict[str, Any]:
    empresa_uuid = context["empresa_id"]
    lead = context["lead"]
    conversation = context["conversation"]
    contact = context["contact"]
    conexao = context["conexao"]
    provider = context["provider"]
    channel = context["channel"]
    destino = context["destination"]

    if str((lead.dados_adicionais or {}).get("origem") or "") == "instagram_comment" and (
        channel != "instagram" or provider != "meta_instagram"
    ):
        raise HTTPException(
            status_code=409,
            detail="Aguarde uma mensagem real no Direct para obter o IGSID antes de responder.",
        )

    if require_client_message_id and not str(payload.client_message_id or "").strip():
        raise HTTPException(status_code=400, detail="Identificador da mensagem obrigatório")

    duplicate = await _lock_and_find_duplicate_inbox_message(
        db,
        context=context,
        client_message_id=payload.client_message_id,
    )
    if duplicate:
        return {
            "status": "success",
            "deduplicated": True,
            "mensagem": _serialize_manual_outbound_message(duplicate),
        }

    external_message_id = None
    message_origin = None
    outbound_payload = StandardOutgoingMessage(
        identificador_contato=destino,
        canal="whatsapp",
        texto=payload.texto,
        tipo="text",
        media_url=None,
    )
    if provider == "meta_whatsapp":
        enviado = await despachar_mensagem(
            canal="whatsapp",
            identificador_origem=destino,
            texto=payload.texto,
            conexao_id=str(conexao.id),
            empresa_id=str(empresa_uuid),
        )
        if not enviado:
            raise HTTPException(status_code=502, detail="Mensagem não despachada pela API Oficial da Meta")
    elif provider == "evolution":
        await dispatch_outbound_message(
            empresa_id=empresa_uuid,
            conexao=conexao,
            payload=outbound_payload,
        )
    elif provider == "meta_instagram":
        if channel != "instagram":
            raise HTTPException(status_code=400, detail="Conversa Direct do Instagram não encontrada")
        from app.services.instagram_simple import InstagramGraphClient, connection_token

        graph_response = await InstagramGraphClient(connection_token(conexao)).direct_reply(
            str(conexao.instagram_business_account_id or "").strip(),
            destino,
            payload.texto,
        )
        external_message_id = str(
            graph_response.get("message_id") or graph_response.get("id") or ""
        ).strip() or None
        message_origin = "instagram_message"
        conversation.last_message_at = datetime.utcnow()
        conversation.last_outbound_at = conversation.last_message_at
    else:
        raise HTTPException(status_code=400, detail="Provider da conversa não suportado para envio manual")

    instagram_history_fields = (
        {
            "external_message_id": external_message_id,
            "external_conversation_id": f"instagram:{destino}",
            "provider_status": "sent",
            "metadata_json": {"origin": message_origin, "instagram_igsid": destino},
        }
        if provider == "meta_instagram"
        else {}
    )
    message_metadata = dict(instagram_history_fields.get("metadata_json") or {})
    if payload.client_message_id:
        message_metadata["inbox_client_message_id"] = payload.client_message_id
    if message_metadata:
        instagram_history_fields["metadata_json"] = message_metadata

    # Persist only after the existing dispatcher confirms successful delivery.
    nova_msg = MensagemHistorico(
        empresa_id=empresa_uuid,
        lead_id=lead.id,
        conexao_id=conexao.id,
        channel_conversation_id=conversation.id,
        channel=channel,
        provider="meta_whatsapp" if provider == "meta_whatsapp" else provider,
        direction="outbound",
        message_status="sent",
        texto=payload.texto,
        tipo_mensagem="text",
        media_url=None,
        origem_mensagem="humano",
        from_me=True,
        **instagram_history_fields,
    )
    db.add(nova_msg)
    lead.ia_ativa = False
    await db.flush()
    try:
        from app.services.followup_engine import registrar_followup_message_event

        await registrar_followup_message_event(
            db,
            empresa_id=empresa_uuid,
            lead=lead,
            contact=contact,
            conversation=conversation,
            conexao_id=conexao.id,
            provider="meta_whatsapp" if provider == "meta_whatsapp" else provider,
            channel=channel,
            from_me=True,
            message=nova_msg,
            message_at=nova_msg.criado_em or datetime.utcnow(),
        )
    except Exception as followup_exc:
        logger.debug("Follow-up inbox text skipped: %s", followup_exc)
    await db.commit()
    await db.refresh(nova_msg)
    mensagem_payload = _serialize_manual_outbound_message(nova_msg)
    await manager.broadcast_to_empresa(
        str(empresa_uuid),
        {
            "type": "inbox.message.created",
            "tipo_evento": "nova_mensagem_outbound",
            "empresa_id": str(empresa_uuid),
            "lead_id": str(lead.id),
            "conversation_id": str(conversation.id),
            "telefone": str(lead.telefone_contato or ""),
            "direction": "outbound",
            "created_at": mensagem_payload["criado_em"],
            "last_message_preview": mensagem_payload["texto"][:240],
            "last_message_at": mensagem_payload["criado_em"],
            "mensagem": mensagem_payload,
        },
    )
    await manager.broadcast_to_empresa(
        str(empresa_uuid),
        {
            "type": "STATUS_IA_CHANGED",
            "payload": {"lead_id": str(lead.id), "ia_ativa": False},
        },
    )
    return {"status": "success", "mensagem": mensagem_payload}


@router.post("/{empresa_id}/inbox/{telefone}/send_media")
async def enviar_midia(
    empresa_id: str,
    telefone: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
    tipo_mensagem: str = Form(""),
    client_message_id: str | None = Form(None, min_length=1, max_length=128),
):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "tipo_mensagem": "document"}
    try:
        async with AsyncSessionLocal() as session:
            context = await _resolve_legacy_send_context(
                session,
                empresa_id=empresa_id,
                telefone=telefone,
            )
            return await _dispatch_manual_media(
                session,
                context=context,
                file=file,
                caption=caption,
                tipo_mensagem=tipo_mensagem,
                client_message_id=client_message_id,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Inbox manual media send failed")
        raise HTTPException(status_code=500, detail="Não foi possível enviar a mídia") from e


@router.post("/{empresa_id}/inbox/leads/{lead_id}/conversations/{conversation_id}/send_media")
async def enviar_midia_por_conversa(
    empresa_id: str,
    lead_id: str,
    conversation_id: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
    tipo_mensagem: str = Form(""),
    client_message_id: str | None = Form(None, min_length=1, max_length=128),
):
    try:
        async with AsyncSessionLocal() as session:
            context = await _resolve_inbox_send_context(
                session,
                empresa_id=empresa_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
            )
            return await _dispatch_manual_media(
                session,
                context=context,
                file=file,
                caption=caption,
                tipo_mensagem=tipo_mensagem,
                client_message_id=client_message_id,
                require_client_message_id=True,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Inbox manual media send failed")
        raise HTTPException(status_code=500, detail="Não foi possível enviar a mídia") from e


async def _dispatch_manual_media(
    session: AsyncSession,
    *,
    context: dict[str, Any],
    file: UploadFile,
    caption: str = "",
    tipo_mensagem: str = "",
    client_message_id: str | None = None,
    require_client_message_id: bool = False,
) -> dict[str, Any]:
    if not file:
        raise HTTPException(status_code=400, detail="Arquivo não informado")
    if context["provider"] != "evolution" or context["channel"] != "whatsapp":
        raise HTTPException(status_code=400, detail="Envio de mídia não disponível para a conexão desta conversa")
    if str(getattr(context["conexao"], "status", "") or "").strip().lower() not in {"ativo", "connected", "conectado", "open"}:
        raise HTTPException(status_code=400, detail="A conexão da conversa não está ativa")
    if require_client_message_id and not str(client_message_id or "").strip():
        raise HTTPException(status_code=400, detail="Identificador da mensagem obrigatório")

    override = str(tipo_mensagem or "").strip().lower()
    permitidos = {"image", "audio", "video", "document"}
    if override in permitidos:
        tipo_resolvido = override
    else:
        tipo_resolvido = _inferir_tipo_mensagem(file.content_type)
    mimetype = (file.content_type or "").strip() or "application/octet-stream"
    if tipo_resolvido == "audio":
        mt = mimetype.lower()
        filename = (file.filename or "").lower()
        if not (mt.startswith("audio/") and "octet-stream" not in mt):
            if mt == "video/webm" or filename.endswith(".webm"):
                mimetype = "audio/webm"
            elif filename.endswith((".ogg", ".opus")) or "ogg" in mt:
                mimetype = "audio/ogg"
            elif not mt.startswith("audio/") or "octet-stream" in mt:
                mimetype = "audio/webm"

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    media_b64 = base64.b64encode(file_bytes).decode("utf-8")

    duplicate = await _lock_and_find_duplicate_inbox_message(
        session,
        context=context,
        client_message_id=client_message_id,
    )
    if duplicate:
        return {
            "status": "success",
            "tipo_mensagem": str(duplicate.tipo_mensagem or tipo_resolvido),
            "deduplicated": True,
            "mensagem": _serialize_manual_outbound_message(duplicate),
        }

    from app.services.evolution_service import enviar_midia_base64
    resposta_envio = await enviar_midia_base64(
        conexao=context["conexao"],
        numero=context["destination"],
        base64_data=media_b64,
        tipo=tipo_resolvido,
        mimetype=mimetype,
        caption=caption or "",
    )
    if not resposta_envio:
        raise HTTPException(status_code=502, detail="Falha ao enviar mídia para a Evolution API")

    lead = context["lead"]
    conversation = context["conversation"]
    contact = context["contact"]
    conexao = context["conexao"]
    empresa_uuid = context["empresa_id"]
    text_value = (caption or "").strip()
    if not text_value:
        text_value = {
            "image": "Imagem enviada",
            "audio": "Áudio enviado",
            "video": "Vídeo enviado",
        }.get(tipo_resolvido, f"Documento enviado: {file.filename or 'arquivo'}")
    metadata = {"inbox_client_message_id": client_message_id} if client_message_id else {}
    nova_msg = MensagemHistorico(
        empresa_id=empresa_uuid,
        lead_id=lead.id,
        conexao_id=conexao.id,
        channel_conversation_id=conversation.id,
        channel=context["channel"],
        provider=str(getattr(conexao, "provider", None) or "evolution_legacy"),
        direction="outbound",
        message_status="sent",
        texto=text_value,
        tipo_mensagem=tipo_resolvido,
        media_url=media_b64,
        metadata_json=metadata,
        origem_mensagem="humano",
        from_me=True,
    )
    session.add(nova_msg)
    lead.ia_ativa = False
    await session.flush()
    try:
        from app.services.followup_engine import registrar_followup_message_event
        from app.services.outbound_origin_tracker import extrair_message_id_resposta_evolution, marcar_outbound_backend

        await marcar_outbound_backend(
            str(empresa_uuid),
            context["destination"],
            message_id=extrair_message_id_resposta_evolution(resposta_envio if isinstance(resposta_envio, dict) else None),
            texto=text_value,
        )
        await registrar_followup_message_event(
            session,
            empresa_id=empresa_uuid,
            lead=lead,
            contact=contact,
            conversation=conversation,
            conexao_id=conexao.id,
            provider=str(getattr(conexao, "provider", None) or "evolution_legacy"),
            channel=context["channel"],
            from_me=True,
            message=nova_msg,
            message_at=nova_msg.criado_em or datetime.utcnow(),
        )
    except Exception as followup_exc:
        logger.debug("Follow-up inbox media skipped: %s", followup_exc)
    await session.commit()
    await session.refresh(nova_msg)

    mensagem_payload = _serialize_manual_outbound_message(nova_msg)
    await manager.broadcast_to_empresa(
        str(empresa_uuid),
        {
            "type": "inbox.message.created",
            "tipo_evento": "nova_mensagem_outbound",
            "empresa_id": str(empresa_uuid),
            "lead_id": str(lead.id),
            "conversation_id": str(conversation.id),
            "telefone": str(lead.telefone_contato or ""),
            "direction": "outbound",
            "created_at": mensagem_payload["criado_em"],
            "last_message_preview": mensagem_payload["texto"][:240],
            "last_message_at": mensagem_payload["criado_em"],
            "mensagem": mensagem_payload,
        },
    )
    await manager.broadcast_to_empresa(
        str(empresa_uuid),
        {"type": "STATUS_IA_CHANGED", "payload": {"lead_id": str(lead.id), "ia_ativa": False}},
    )
    return {"status": "success", "tipo_mensagem": tipo_resolvido, "mensagem": mensagem_payload}

@router.post("/{empresa_id}/inbox/{telefone}/reativar_bot")
async def reativar_bot(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "message": "Bot reativado"}
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")
            
            lead.ia_ativa = True

            from app.services.crm_etapas_service import obter_ou_criar_etapa_por_tipo

            etapa_atend = await obter_ou_criar_etapa_por_tipo(session, empresa_uuid, "atendimento")
            if etapa_atend:
                lead.etapa_id = etapa_atend

            await session.commit()
            await manager.broadcast_to_empresa(
                empresa_id,
                {
                    "type": "STATUS_IA_CHANGED",
                    "tipo_evento": "status_ia_changed",
                    "lead_id": str(lead.id),
                    "payload": {"lead_id": str(lead.id), "ia_ativa": True},
                },
            )
            return {"status": "success", "message": "Bot reativado", "ia_ativa": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{empresa_id}/inbox/{telefone}/pausar_bot")
async def pausar_bot(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "message": "Bot pausado"}
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")

            lead.ia_ativa = False
            await session.commit()
            await manager.broadcast_to_empresa(
                empresa_id,
                {
                    "type": "STATUS_IA_CHANGED",
                    "tipo_evento": "status_ia_changed",
                    "lead_id": str(lead.id),
                    "payload": {"lead_id": str(lead.id), "ia_ativa": False},
                },
            )
            return {
                "status": "success",
                "message": "Bot pausado",
                "ia_ativa": False,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
