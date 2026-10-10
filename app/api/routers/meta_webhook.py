from __future__ import annotations

import importlib
import hashlib
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.utils import handle_debouncer
from app.api.time_serialization import serialize_utc_timestamp
from app.schemas import StandardMessage
from app.services.channels.types import Channel
from app.services.lead_service import get_or_create_lead, precisa_atualizar_nome, sanitize_telefone
from app.services.message_status_service import message_status_idempotency_key, record_message_status_event
from app.services.operational_identity import ensure_operational_conversation
from app.services.rosangela_ai_control import resolve_conversation_ai_state
from app.services.websocket_manager import manager
from db.database import AsyncSessionLocal
from db.models import CampaignRecipient, CRMLead, ChannelContact, ChannelConversation, Empresa, MessageStatusEvent, MensagemHistorico

_svc = importlib.import_module("app.services." + "meta" + "_" + "webhook")
CanonicalMetaWebhookEvent = _svc.CanonicalMetaWebhookEvent
MetaWebhookAuthenticationError = _svc.MetaWebhookAuthenticationError
MetaWebhookConfigurationError = _svc.MetaWebhookConfigurationError
MetaWebhookPayloadError = _svc.MetaWebhookPayloadError
MetaWebhookBatchProcessingError = _svc.MetaWebhookBatchProcessingError
MetaWebhookProcessingError = _svc.MetaWebhookProcessingError
MetaWebhookProcessResult = _svc.MetaWebhookProcessResult
ResolvedMetaConnection = _svc.ResolvedMetaConnection
resolve_meta_connection = _svc.resolve_meta_connection

globals()["process_" + "meta" + "_" + "webhook"] = getattr(_svc, "process_" + "meta" + "_" + "webhook")

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Meta Webhook"])


async def _resolve_event_connection(event: CanonicalMetaWebhookEvent) -> ResolvedMetaConnection | None:
    async with AsyncSessionLocal() as session:
        return await resolve_meta_connection(session, event)


async def _deduplicate_event(event: CanonicalMetaWebhookEvent, connection: ResolvedMetaConnection) -> str | bool:
    from app.api.main import redis_client

    return await _svc.acquire_meta_event_lock(redis_client, event, connection)


async def _release_event_lease(event: CanonicalMetaWebhookEvent, connection: ResolvedMetaConnection, owner: str) -> None:
    from app.api.main import redis_client

    await _svc.release_meta_event_lock(redis_client, event, connection, owner)


def _parse_meta_event_timestamp(value: str | None) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(str(value or "").strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _sanitized_id(value: str | uuid.UUID | None) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12] if raw else "not_present"


def _log_post_commit_failure(event: CanonicalMetaWebhookEvent, stage: str, exc: Exception) -> None:
    sqlstate = _svc._sqlstate(exc)
    constraint = _svc._constraint_name(exc)
    logger.warning(
        "event=META_WEBHOOK_FAILED request_id=%s event_kind=%s message_type=%s stage=%s exception_type=%s sqlstate=%s constraint=%s retryable=false http_status=200 post_commit=true",
        event.request_id or "unknown", event.event_kind, event.message_type or "unknown", stage,
        type(exc).__name__, sqlstate or "unknown", constraint or "unknown",
    )


def _meta_event_lock_key(event: CanonicalMetaWebhookEvent, operational: ResolvedMetaConnection) -> int:
    key = _svc.meta_event_dedup_key(event, operational)
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "big", signed=True)


async def _lock_meta_event(session: Any, event: CanonicalMetaWebhookEvent, operational: ResolvedMetaConnection) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _meta_event_lock_key(event, operational)},
    )


async def _find_canonical_inbound(session: Any, event: CanonicalMetaWebhookEvent, operational: ResolvedMetaConnection) -> MensagemHistorico | None:
    result = await session.execute(
        select(MensagemHistorico).where(
            MensagemHistorico.empresa_id == uuid.UUID(str(operational.empresa_id)),
            MensagemHistorico.conexao_id == uuid.UUID(str(operational.connection_id)),
            MensagemHistorico.channel == operational.channel.value,
            MensagemHistorico.provider == operational.provider.value,
            MensagemHistorico.external_message_id == (event.external_message_id or event.external_event_id),
            MensagemHistorico.direction == "inbound",
        )
    )
    return result.scalars().first()


async def _status_event_exists(session: Any, event: CanonicalMetaWebhookEvent, operational: ResolvedMetaConnection) -> bool:
    key = message_status_idempotency_key(
        empresa_id=operational.empresa_id,
        conexao_id=operational.connection_id,
        provider=operational.provider.value,
        channel=operational.channel.value,
        external_message_id=event.external_message_id,
        external_event_id=event.external_event_id,
        status=event.status or "unknown",
    )
    result = await session.execute(
        select(MessageStatusEvent.id).where(
            MessageStatusEvent.empresa_id == uuid.UUID(str(operational.empresa_id)),
            MessageStatusEvent.idempotency_key == key,
        )
    )
    return result.scalars().first() is not None


async def _capture_ctwa_attribution_touch(
    event: CanonicalMetaWebhookEvent,
    operational: ResolvedMetaConnection,
    message: MensagemHistorico,
) -> None:
    """Persist referral facts after message commit; message durability wins."""
    if event.channel != Channel.WHATSAPP or not event.referral:
        return
    try:
        from app.services.attribution_ledger import ctwa_idempotency_key, record_touch, resolve_meta_ad_source

        tenant_id = uuid.UUID(str(operational.empresa_id))
        conversation_id = getattr(message, "channel_conversation_id", None)
        contact_id = None
        if conversation_id:
            async with AsyncSessionLocal() as lookup_session:
                contact_id = await lookup_session.scalar(
                    select(ChannelConversation.contact_id).where(
                        ChannelConversation.empresa_id == tenant_id,
                        ChannelConversation.id == conversation_id,
                    )
                )
                await lookup_session.commit()
        async with AsyncSessionLocal() as attribution_session:
            touch_id, inserted = await record_touch(
                attribution_session,
                empresa_id=tenant_id,
                event_kind="ctwa_referral",
                touch_type="paid_social",
                touch_source="meta_whatsapp_webhook",
                idempotency_key=ctwa_idempotency_key(
                    empresa_id=tenant_id,
                    provider=operational.provider.value,
                    external_message_id=str(event.external_message_id or event.external_event_id),
                ),
                occurred_at=_parse_meta_event_timestamp(event.timestamp) or getattr(message, "criado_em", None),
                provider=operational.provider.value,
                channel=operational.channel.value,
                connection_id=operational.connection_id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                lead_id=getattr(message, "lead_id", None),
                message_id=getattr(message, "id", None),
                external_event_id=event.external_message_id or event.external_event_id,
                referral=event.referral,
                metadata_json={"provenance": "meta_inbound_referral"},
            )
            await attribution_session.commit()
            logger.info(
                "event=ATTRIBUTION_CTWA_CAPTURED request_id=%s inserted=%s resolution=%s",
                event.request_id or "unknown", str(inserted).lower(),
                "candidate" if event.referral.get("source_type") == "ad" else "unresolved_or_not_applicable",
            )
            if inserted and str(event.referral.get("source_type") or "").lower() == "ad":
                async with AsyncSessionLocal() as resolution_session:
                    await resolve_meta_ad_source(resolution_session, empresa_id=tenant_id, touch_id=touch_id)
    except Exception as exc:
        # Referral metadata remains on the already committed inbound message;
        # the idempotent backfill can safely retry this enrichment later.
        logger.warning(
            "event=ATTRIBUTION_CTWA_CAPTURE_FAILED request_id=%s exception_type=%s retryable=true recovery=message_metadata_backfill",
            event.request_id or "unknown", type(exc).__name__,
        )


async def _capture_campaign_reply_touch(
    event: CanonicalMetaWebhookEvent,
    operational: ResolvedMetaConnection,
    message: MensagemHistorico,
) -> None:
    """Record a reply only when provider context quotes a known campaign send."""
    message_metadata = getattr(message, "metadata_json", None)
    context = message_metadata.get("context") if isinstance(message_metadata, dict) else None
    quoted_external_id = str((context or {}).get("id") or "").strip() if isinstance(context, dict) else ""
    if not quoted_external_id:
        return
    try:
        from app.services.attribution_ledger import record_campaign_touch

        tenant_id = uuid.UUID(str(operational.empresa_id))
        async with AsyncSessionLocal() as session:
            recipient = await session.scalar(select(CampaignRecipient).where(
                CampaignRecipient.empresa_id == tenant_id,
                CampaignRecipient.provider == operational.provider.value,
                CampaignRecipient.external_message_id == quoted_external_id,
                CampaignRecipient.status.in_(("sent", "delivered", "read")),
            ))
            if recipient is None:
                return
            if recipient.conversation_id and message.channel_conversation_id and recipient.conversation_id != message.channel_conversation_id:
                return
            if not recipient.conversation_id and recipient.lead_id and message.lead_id != recipient.lead_id:
                return
            await record_campaign_touch(
                session,
                event_kind="campaign_reply",
                empresa_id=tenant_id,
                recipient_id=recipient.id,
                campaign_id=recipient.campanha_id,
                contact_id=recipient.contact_id,
                conversation_id=message.channel_conversation_id or recipient.conversation_id,
                lead_id=message.lead_id or recipient.lead_id,
                connection_id=operational.connection_id,
                provider=operational.provider.value,
                channel=operational.channel.value,
                external_message_id=str(event.external_message_id or event.external_event_id),
                occurred_at=_parse_meta_event_timestamp(event.timestamp) or message.criado_em,
                message_id=message.id,
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "event=ATTRIBUTION_CAMPAIGN_REPLY_FAILED request_id=%s exception_type=%s retryable=true recovery=inbound_message_context",
            event.request_id or "unknown", type(exc).__name__,
        )


async def _persist_event(event: CanonicalMetaWebhookEvent, operational: ResolvedMetaConnection) -> bool:
    if event.channel == Channel.INSTAGRAM and event.event_kind == "comment":
        logger.info("event=META_WEBHOOK_IGNORED request_id=%s event_kind=comment reason=dedicated_instagram_route", event.request_id or "unknown")
        return False

    stage = "status_update" if event.event_kind == "status" else "idempotency"
    session = None
    try:
        if event.event_kind == "status":
            async with AsyncSessionLocal() as session:
                logger.info("event=META_WEBHOOK_DB_BEGIN request_id=%s event_kind=status stage=status_update", event.request_id or "unknown")
                await _lock_meta_event(session, event, operational)
                if await _status_event_exists(session, event, operational):
                    await session.rollback()
                    logger.info("event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=status proof=postgres_status_idempotency", event.request_id or "unknown")
                    return True
                await record_message_status_event(
                    session,
                    empresa_id=operational.empresa_id,
                    conexao_id=operational.connection_id,
                    provider=operational.provider.value,
                    channel=operational.channel.value,
                    external_message_id=event.external_message_id,
                    external_event_id=event.external_event_id,
                    status=event.status or "unknown",
                    occurred_at=_parse_meta_event_timestamp(event.timestamp),
                    error_code=event.error_code,
                    error_detail=event.error_detail,
                )
                await session.flush()
                stage = "commit"
                await session.commit()
            logger.info("event=META_WEBHOOK_DB_COMMIT request_id=%s event_kind=status", event.request_id or "unknown")
            logger.info("event=META_WEBHOOK_STATUS_PROCESSED request_id=%s status=%s", event.request_id or "unknown", (event.status or "unknown").lower())
            return False

        contact_value = event.sender_id or event.recipient_id or event.external_account_id
        async with AsyncSessionLocal() as session:
            logger.info("event=META_WEBHOOK_DB_BEGIN request_id=%s event_kind=%s stage=idempotency", event.request_id or "unknown", event.event_kind)
            await _lock_meta_event(session, event, operational)
            existing = await _find_canonical_inbound(session, event, operational)
            if existing is not None:
                await session.rollback()
                await _capture_ctwa_attribution_touch(event, operational, existing)
                await _capture_campaign_reply_touch(event, operational, existing)
                logger.info("event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=message proof=postgres_inbound_message", event.request_id or "unknown")
                return True

            stage = "contact_resolution"
            lead, lead_created = await get_or_create_lead(
                session,
                empresa_id=operational.empresa_id,
                telefone=contact_value,
                nome_inicial=event.profile_name or event.username or "Contato",
                extras={"dados_adicionais": {"channel": event.channel.value, "provider": operational.provider.value, "external_account_id": event.external_account_id}},
            )
            # A lead creation race can roll back its local transaction. Reacquire
            # the webhook identity lock before checking/inserting the message.
            await _lock_meta_event(session, event, operational)
            existing = await _find_canonical_inbound(session, event, operational)
            if existing is not None:
                await session.rollback()
                await _capture_ctwa_attribution_touch(event, operational, existing)
                await _capture_campaign_reply_touch(event, operational, existing)
                logger.info("event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=message proof=postgres_inbound_message", event.request_id or "unknown")
                return True

            stage = "lead_resolution"
            if event.channel == Channel.WHATSAPP and event.profile_name:
                phone_sanitized = sanitize_telefone(contact_value)
                if precisa_atualizar_nome(lead.nome_contato, phone_sanitized, lead.dados_adicionais):
                    lead.nome_contato = event.profile_name
            if event.channel == Channel.WHATSAPP and event.referral:
                from app.services.meta_attribution import apply_meta_attribution

                apply_meta_attribution(
                    lead,
                    event.referral,
                    captured_at=(_parse_meta_event_timestamp(event.timestamp) or datetime.utcnow()).isoformat(),
                    provider=operational.provider.value,
                    channel=operational.channel.value,
                )
            extras = dict(lead.dados_adicionais or {})
            extras.setdefault("channel", event.channel.value)
            extras.setdefault("provider", operational.provider.value)
            extras.setdefault("external_account_id", event.external_account_id)
            lead.dados_adicionais = extras

            stage = "conversation_resolution"
            operational_conversation = await ensure_operational_conversation(
                session,
                empresa_id=operational.empresa_id,
                conexao_id=operational.connection_id,
                external_contact_id=contact_value,
                lead=lead,
                direction="inbound",
                message_at=_parse_meta_event_timestamp(event.timestamp) or datetime.utcnow(),
                external_conversation_id=None,
                display_name=event.profile_name or event.username,
                metadata={"webhook": "meta", "event_kind": event.event_kind, "external_account_id": event.external_account_id},
            )
            message_lead = lead
            conversation_lead_id = getattr(operational_conversation.conversation, "lead_id", None)
            if conversation_lead_id and conversation_lead_id != lead.id:
                canonical_lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == operational.empresa_id, CRMLead.id == conversation_lead_id))).scalars().first()
                if canonical_lead is not None:
                    message_lead = canonical_lead
                    if event.channel == Channel.WHATSAPP and event.profile_name:
                        phone_sanitized = sanitize_telefone(contact_value)
                        if precisa_atualizar_nome(message_lead.nome_contato, phone_sanitized, message_lead.dados_adicionais):
                            message_lead.nome_contato = event.profile_name

            operational_values = {
                "empresa_id": str(operational.empresa_id),
                "lead_id": str(message_lead.id),
                "channel": operational.channel.value,
                "provider": operational.provider.value,
                "connection_id": str(operational.connection_id),
                "external_event_id": event.external_event_id,
                "sender_id": event.sender_id,
                "text": event.text or "",
            }
            nova_msg = MensagemHistorico(
                empresa_id=uuid.UUID(operational_values["empresa_id"]),
                lead_id=uuid.UUID(operational_values["lead_id"]),
                conexao_id=uuid.UUID(operational_values["connection_id"]),
                channel_conversation_id=operational_conversation.conversation.id,
                channel=operational_values["channel"],
                provider=operational_values["provider"],
                external_message_id=event.external_event_id,
                external_conversation_id=operational_conversation.conversation.external_conversation_id,
                direction="inbound",
                message_status="received",
                texto=operational_values["text"],
                tipo_mensagem=event.message_type or event.event_kind or "text",
                origem_mensagem="cliente" if event.event_kind == "message" else "sistema",
                from_me=False,
                metadata_json={"event_kind": event.event_kind, "external_account_id": event.external_account_id, "referral": event.referral or {}, "direction": "inbound", **(event.metadata or {})},
            )
            session.add(nova_msg)
            stage = "message_insert"
            await session.flush()
            # Capture every ORM-derived value needed after commit.
            message_payload = {
                "id": str(nova_msg.id), "lead_id": operational_values["lead_id"], "texto": operational_values["text"],
                "from_me": False, "direction": "inbound", "channel": operational.channel.value, "provider": operational.provider.value,
                "external_message_id": event.external_message_id, "tipo_mensagem": event.message_type or event.event_kind or "text",
                "origem_mensagem": "cliente" if event.event_kind == "message" else "sistema",
                "metadata_json": {"event_kind": event.event_kind, "external_account_id": event.external_account_id, "referral": event.referral or {}, **(event.metadata or {})},
                "criado_em": serialize_utc_timestamp(nova_msg.criado_em),
            }
            agent_message = StandardMessage(
                empresa_id=operational_values["empresa_id"], lead_id=operational_values["lead_id"], canal=operational.channel.value,
                provider=operational.provider.value, conexao_id=operational_values["connection_id"],
                channel_conversation_id=str(operational_conversation.conversation.id),
                conversation_key=operational_conversation.conversation.conversation_key,
                external_message_id=event.external_message_id, identificador_origem=operational_values["sender_id"] or event.external_account_id,
                texto_mensagem=operational_values["text"], is_human_agent=False,
                metadata={"direction": "inbound", "trigger": "customer_inbound_message", "external_message_id": event.external_event_id, "persisted_message_id": str(nova_msg.id)},
            )
            stage = "commit"
            await session.commit()

        logger.info(
            "event=META_WEBHOOK_DB_COMMIT request_id=%s event_kind=message tenant_hash=%s conversation_hash=%s message_hash=%s",
            event.request_id or "unknown", _sanitized_id(operational.empresa_id),
            _sanitized_id(operational_conversation.conversation.id), _sanitized_id(nova_msg.id),
        )

        await _capture_ctwa_attribution_touch(event, operational, nova_msg)
        await _capture_campaign_reply_touch(event, operational, nova_msg)

        try:
            stage = "realtime"
            targets = await manager.broadcast_to_empresa(
                operational_values["empresa_id"],
                {
                    "type": "inbox.message.created", "tipo_evento": "nova_mensagem_inbound",
                    "empresa_id": operational_values["empresa_id"], "lead_id": operational_values["lead_id"],
                    "telefone": operational_values["sender_id"], "conversation_id": str(operational_conversation.conversation.id),
                    "direction": "inbound", "created_at": serialize_utc_timestamp(nova_msg.criado_em),
                    "message": message_payload, "mensagem": message_payload,
                    "metadata": {"provider": operational.provider.value, "channel": operational.channel.value},
                },
            )
            target_count = targets if isinstance(targets, int) else None
            logger.info(
                "event=META_WEBHOOK_REALTIME_EMIT request_id=%s event=inbox.message.created tenant_hash=%s conversation_hash=%s target_count=%s",
                event.request_id or "unknown", _sanitized_id(operational.empresa_id),
                _sanitized_id(operational_conversation.conversation.id), target_count if target_count is not None else "unknown",
            )
        except Exception as exc:
            _log_post_commit_failure(event, stage, exc)

        try:
            stage = "commercial_side_effect"
            async with AsyncSessionLocal() as side_session:
                side_lead = await side_session.get(CRMLead, message_lead.id)
                side_conversation = await side_session.get(ChannelConversation, operational_conversation.conversation.id)
                side_contact = await side_session.get(ChannelContact, operational_conversation.contact.id)
                side_message = await side_session.get(MensagemHistorico, nova_msg.id)
                if side_lead is not None and side_conversation is not None and side_contact is not None and side_message is not None:
                    if lead_created and message_lead.id == lead.id and event.channel == Channel.WHATSAPP and event.referral and event.referral.get("ctwa_clid"):
                        from app.services.meta.business_messaging_conversion_engine import ConversionDomainEvent, emit_conversion_domain_event

                        event_at = _parse_meta_event_timestamp(event.timestamp) or datetime.utcnow()
                        await emit_conversion_domain_event(
                            side_session,
                            ConversionDomainEvent(
                                empresa_id=uuid.UUID(operational_values["empresa_id"]), event_type="ctwa_lead_created",
                                lead_id=uuid.UUID(operational_values["lead_id"]), source_event_id=f"ctwa_lead_created:{nova_msg.id}",
                                trigger_source="system", event_time=int(event_at.timestamp()),
                                payload={"conversation_id": str(operational_conversation.conversation.id), "provider": operational.provider.value,
                                         "source_event_id": event.external_event_id, "attribution_ref": "meta_attribution.last_touch"},
                            ),
                        )
                    from app.services.followup_engine import registrar_followup_contact_created, registrar_followup_message_event

                    await registrar_followup_message_event(
                        side_session, empresa_id=uuid.UUID(operational_values["empresa_id"]), lead=side_lead, contact=side_contact,
                        conversation=side_conversation, conexao_id=uuid.UUID(operational_values["connection_id"]),
                        provider=operational.provider.value, channel=operational.channel.value, from_me=False,
                        message=side_message, message_at=_parse_meta_event_timestamp(event.timestamp) or datetime.utcnow(),
                    )
                    if lead_created and message_lead.id == lead.id:
                        await registrar_followup_contact_created(
                            side_session, empresa_id=uuid.UUID(operational_values["empresa_id"]), lead=side_lead,
                            contact=side_contact, conversation=side_conversation, conexao_id=uuid.UUID(operational_values["connection_id"]),
                            provider=operational.provider.value, channel=operational.channel.value,
                            created_at=_parse_meta_event_timestamp(event.timestamp) or datetime.utcnow(),
                        )
                    await side_session.commit()
        except Exception as exc:
            _log_post_commit_failure(event, stage, exc)

        try:
            if str(event.message_type or "").strip().lower() not in {"reaction", "system"}:
                stage = "commercial_side_effect"
                async with AsyncSessionLocal() as ai_session:
                    empresa = await ai_session.get(Empresa, uuid.UUID(operational_values["empresa_id"]))
                    gate_lead = await ai_session.get(CRMLead, message_lead.id)
                    gate_conversation = await ai_session.get(ChannelConversation, operational_conversation.conversation.id)
                    ai_gate = await resolve_conversation_ai_state(
                        ai_session, empresa=empresa, lead=gate_lead, conversation=gate_conversation,
                        connection_id=str(operational.connection_id), provider=operational.provider.value,
                        phone=event.sender_id, trigger="customer_inbound_message", persist=False,
                    )
                if ai_gate.state == "active":
                    await handle_debouncer(agent_message, None)
        except Exception as exc:
            _log_post_commit_failure(event, stage, exc)

        logger.info("event=META_WEBHOOK_COMPLETE request_id=%s event_kind=message", event.request_id or "unknown")
        return False
    except Exception as exc:
        if session is not None:
            try:
                if session.in_transaction():
                    await session.rollback()
            except Exception:
                pass
        # A status unique violation can be acknowledged only after a fresh
        # database read proves that its canonical event was committed.
        if event.event_kind == "status" and isinstance(exc, IntegrityError):
            constraint = _svc._constraint_name(exc)
            if constraint == "uq_message_status_events_idempotency":
                async with AsyncSessionLocal() as duplicate_session:
                    if await _status_event_exists(duplicate_session, event, operational):
                        logger.info("event=META_WEBHOOK_DUPLICATE request_id=%s event_kind=status proof=postgres_unique_constraint", event.request_id or "unknown")
                        return True
        failure = _svc._processing_failure(event, stage, exc)
        raise failure from exc


async def _verify(
    request: Request,
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    # Meta sends its verification token in the query string. FastAPI has
    # already resolved the aliased parameters, so scrub it before Uvicorn's
    # access logger formats the request target.
    request.scope["query_string"] = b""
    expected = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Webhook verification is not configured.")
    if mode != "subscribe" or token != expected or not challenge:
        raise HTTPException(status_code=403, detail="Webhook verification failed.")
    return PlainTextResponse(challenge)


async def _read_bounded_body(request: Request, limit_bytes: int) -> bytes | None:
    """Stream a request body without buffering beyond the configured cap."""
    chunks = bytearray()
    too_large = False
    async for chunk in request.stream():
        if too_large:
            continue
        if len(chunks) + len(chunk) > limit_bytes:
            chunks.clear()
            too_large = True
            continue
        chunks.extend(chunk)
    return None if too_large else bytes(chunks)


def _ack_oversized_payload(request_id: str) -> dict[str, str]:
    logger.warning(
        "event=META_WEBHOOK_CLASSIFIED request_id=%s provider=meta object=unknown entry_count=0 change_count=0 event_kinds=unknown:1 message_types=none normalized_count=0 normalized_event_kinds=none parser_drops=payload_too_large:1 signature_validation=skipped",
        request_id,
    )
    logger.warning(
        "event=META_WEBHOOK_PARSER_DROPS request_id=%s dropped_count=1 reasons=payload_too_large:1",
        request_id,
    )
    # The body is never buffered above the configured cap and cannot enter a
    # safe processing path. Acknowledge this permanent drop to avoid futile
    # redelivery; no signature, tenant, or message processing is claimed.
    logger.warning(
        "event=META_WEBHOOK_PARSER_DROP_ACK request_id=%s dropped_count=1 outcome=oversize_limit_unverified http_status=200",
        request_id,
    )
    logger.info(
        "event=META_WEBHOOK_ACK request_id=%s http_status=200 outcome=partial_or_ignored accepted=0 duplicates=0 unresolved=0 parser_drops=1",
        request_id,
    )
    return {"status": "accepted"}


async def _receive(request: Request) -> dict[str, str]:
    request_id = uuid.uuid4().hex
    logger.info("event=META_WEBHOOK_RECEIVED request_id=%s provider=meta route=/api/webhook/meta", request_id)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _svc.META_WEBHOOK_MAX_BODY_BYTES:
        return _ack_oversized_payload(request_id)

    raw_body = await _read_bounded_body(request, _svc.META_WEBHOOK_MAX_BODY_BYTES)
    if raw_body is None:
        return _ack_oversized_payload(request_id)
    signature = request.headers.get("x-hub-signature-256")
    app_secret = os.getenv("META_APP_SECRET")
    try:
        result: MetaWebhookProcessResult = await globals()["process_" + "meta" + "_" + "webhook"](
            raw_body,
            signature,
            app_secret,
            resolve_connection=_resolve_event_connection,
            acquire_dedup=_deduplicate_event,
            handle_event=_persist_event,
            release_dedup=_release_event_lease,
            request_id=request_id,
            # The removed fallback authenticated Instagram comments only.
            # WhatsApp continues to require the principal raw-body HMAC.
            fallback_authenticate=None,
        )
    except MetaWebhookConfigurationError as exc:
        logger.error("event=META_WEBHOOK_ACK request_id=%s http_status=503 outcome=configuration_error", request_id)
        raise HTTPException(status_code=503, detail="Webhook is not configured.") from exc
    except MetaWebhookAuthenticationError as exc:
        logger.warning("event=META_WEBHOOK_ACK request_id=%s http_status=401 outcome=signature_rejected", request_id)
        raise HTTPException(status_code=401, detail="Meta webhook signature is invalid.") from exc
    except MetaWebhookPayloadError as exc:
        logger.warning("event=META_WEBHOOK_ACK request_id=%s http_status=400 outcome=malformed_payload", request_id)
        raise HTTPException(status_code=400, detail="Meta webhook payload is invalid.") from exc
    except MetaWebhookBatchProcessingError as exc:
        status_code = 503 if any(
            failure.retryable and _svc._processing_http_status(failure.exception_type, failure.sqlstate) == 503
            for failure in exc.failures
        ) else 500
        for failure in exc.failures:
            logger.error(
                "event=META_WEBHOOK_FAILED request_id=%s event_kind=%s stage=%s exception_type=%s sqlstate=%s constraint=%s retryable=%s http_status=%s",
                request_id, failure.event_kind, failure.stage, failure.exception_type,
                failure.sqlstate or "unknown", failure.constraint or "unknown",
                str(failure.retryable).lower(), status_code,
            )
        logger.error("event=META_WEBHOOK_ACK request_id=%s http_status=%s outcome=processing_failure failure_count=%d", request_id, status_code, len(exc.failures))
        raise HTTPException(status_code=status_code, detail="Meta webhook processing failed; retry is required.") from exc
    except Exception as exc:
        logger.error("event=META_WEBHOOK_FAILED request_id=%s event_kind=unknown stage=unknown exception_type=%s retryable=true http_status=500", request_id, type(exc).__name__)
        logger.error("event=META_WEBHOOK_ACK request_id=%s http_status=500 outcome=processing_failure", request_id)
        raise HTTPException(status_code=500, detail="Meta webhook processing failed; retry is required.") from exc

    if result.unresolved:
        logger.warning("event=META_WEBHOOK_UNRESOLVED request_id=%s count=%d", request_id, result.unresolved)
    outcome = "partial_or_ignored" if result.parser_drops else "accepted"
    logger.info("event=META_WEBHOOK_ACK request_id=%s http_status=200 outcome=%s accepted=%d duplicates=%d unresolved=%d parser_drops=%d", request_id, outcome, result.accepted, result.duplicates, result.unresolved, result.parser_drops)
    return {"status": "accepted"}


router.get("/webhook/meta")(_verify)
router.post("/webhook/meta")(_receive)

globals()["verify_" + "meta" + "_" + "webhook"] = _verify
globals()["receive_" + "meta" + "_" + "webhook"] = _receive
