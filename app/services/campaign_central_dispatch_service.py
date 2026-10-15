from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import redis.asyncio as redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.secret_sanitizer import sanitize_for_log
from app.services.campaign_audience_service import normalize_phone
from app.services.campaign_central_service import (
    build_meta_template_send_payload,
    normalize_campaign_variable_bindings,
    parse_template_components,
    resolve_campaign_template_parameters_for_lead,
    render_campaign_history_content,
    template_requires_header_image,
)
from app.services.channels.contracts import ChannelConnectionContext, ChannelOutboundMessage
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppConfig, MetaWhatsAppError, MetaWhatsAppProvider
from app.services.channels.types import Channel, Provider
from app.services.operational_identity import ensure_operational_conversation
from app.services.rosangela_ai_control import initialize_conversation_ai_from_campaign
from app.services.websocket_manager import manager
from app.api.time_serialization import serialize_utc_timestamp
from app.services.outbox_service import (
    communication_allowed,
    deterministic_idempotency_key,
    enqueue_message,
    record_attempt,
    sanitize_error_detail,
)
from db.database import AsyncSessionLocal
from db.models import (
    CRMLead,
    CampaignRecipient,
    CampanhaDisparo,
    CampanhaDisparoStatus,
    ChannelConversation,
    CommunicationConsent,
    Conexao,
    MessageOutbox,
    MensagemHistorico,
    MessageStatusEvent,
    MetaMessageTemplate,
)


CAMPAIGN_TERMINAL_RECIPIENT_STATUSES = {"sent", "delivered", "read", "failed", "blocked", "cancelled", "stopped"}
CAMPAIGN_ACTIVE_STATUSES = {"queued", "running"}
CAMPAIGN_STARTABLE_STATUSES = {"draft", "prepared", "queued", "running"}
META_RATE_LIMIT_ERROR_CODES = {"4", "80007", "130429", "131048", "131056"}
META_QUALITY_ERROR_CODES = {"131031", "131048"}
DEFAULT_AGENTOS_MPS = 10
META_DEFAULT_MPS = 80
META_MAX_MPS = 1000
MAX_SEND_ATTEMPTS = 3
_MEMORY_RATE_BUCKETS: dict[str, tuple[int, int]] = {}
logger = logging.getLogger(__name__)
worker_logger = logging.getLogger("uvicorn.error")
_MEMORY_RATE_LOCK = asyncio.Lock()
_REDIS_RATE_CLIENT: redis.Redis | None = None


class CampaignCentralError(ValueError):
    pass


def _parse_start_confirmation(
    *,
    snapshot_id: uuid.UUID | str | None,
    confirmed_recipient_count: int | str | None,
) -> tuple[uuid.UUID, int]:
    if snapshot_id is None or str(snapshot_id).strip() == "":
        raise CampaignCentralError("Confirmação do snapshot preparado é obrigatória.")
    try:
        parsed_snapshot_id = uuid.UUID(str(snapshot_id))
    except (TypeError, ValueError) as exc:
        raise CampaignCentralError("snapshot_id inválido para confirmação de início.") from exc
    try:
        parsed_count = int(confirmed_recipient_count)
    except (TypeError, ValueError) as exc:
        raise CampaignCentralError("confirmed_recipient_count inválido para confirmação de início.") from exc
    if parsed_count < 0:
        raise CampaignCentralError("confirmed_recipient_count inválido para confirmação de início.")
    return parsed_snapshot_id, parsed_count


class _CampaignMetaTransport:
    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, json=json)

    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers, params=params)

    async def post_multipart(self, url: str, *, headers: dict[str, str], data: dict[str, Any], files: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, data=data, files=files)


def _provider_for_connection(connection: Conexao) -> MetaWhatsAppProvider:
    context = ChannelConnectionContext(
        empresa_id=str(connection.empresa_id),
        connection_id=str(connection.id),
        channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP,
        status=str(connection.status or "connected"),
        external_account_id=str(connection.phone_number_id or ""),
        metadata={"waba_id": connection.waba_id, "nome_instancia": connection.nome_instancia},
    )
    return MetaWhatsAppProvider(context, connection=connection, transport=_CampaignMetaTransport(), config=MetaWhatsAppConfig(max_attempts=1))


def _is_meta_official(connection: Conexao) -> bool:
    provider = str(getattr(connection, "provider", "") or "").strip().lower()
    tipo = str(getattr(getattr(connection, "tipo", ""), "value", getattr(connection, "tipo", "")) or "").strip().lower()
    return provider in {"meta", "meta_official", "meta_whatsapp", "whatsapp_meta_official"} or tipo == "meta"


def campaign_real_send_allowlist() -> set[str]:
    raw = os.getenv("CAMPAIGN_REAL_SEND_ALLOWLIST", "")
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


def campaign_real_send_allowed(address: str) -> bool:
    allowlist = campaign_real_send_allowlist()
    clean = str(address or "").strip()
    if not clean:
        return False
    if clean in allowlist:
        return True
    digits = "".join(ch for ch in clean if ch.isdigit())
    if digits and digits in allowlist:
        return True
    digest = hashlib.sha256(clean.encode()).hexdigest()
    return digest in allowlist


def campaign_send_mode(connection: Conexao) -> str:
    """Return the connection-scoped mode, failing closed to test_only."""
    credentials = dict(getattr(connection, "credenciais", None) or {})
    raw = credentials.get("campaign_send_mode")
    return "production" if str(raw or "").strip().lower() in {"production", "live"} else "test_only"


def campaign_allowlist_enforced(connection: Conexao) -> bool:
    return campaign_send_mode(connection) != "production"


def campaign_worker_batch_size() -> int:
    try:
        return max(1, min(100, int(os.getenv("CAMPAIGN_WORKER_BATCH_SIZE", "25"))))
    except ValueError:
        return 25


def _configured_connection_mps(connection: Conexao) -> int:
    credentials = dict(getattr(connection, "credenciais", None) or {})
    raw = (
        credentials.get("whatsapp_max_mps")
        or credentials.get("campaign_whatsapp_max_mps")
        or os.getenv("CAMPAIGN_WHATSAPP_DEFAULT_MPS")
        or str(DEFAULT_AGENTOS_MPS)
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_AGENTOS_MPS
    return max(1, min(META_DEFAULT_MPS, value))


def _jittered_retry_at(*, attempts: int, now: datetime | None = None) -> datetime:
    now = now or datetime.utcnow()
    base = min(900, 10 * (2 ** max(0, int(attempts) - 1)))
    return now + timedelta(seconds=base + random.randint(0, max(1, min(30, base))))


def _classify_meta_send_error(exc: MetaWhatsAppError) -> tuple[str, str]:
    status = int(getattr(exc, "http_status", 0) or 0)
    meta_error = dict(getattr(exc, "meta_error", None) or {})
    code = str(meta_error.get("code") or "").strip()
    subcode = str(meta_error.get("error_subcode") or "").strip()
    if status == 429 or code in META_RATE_LIMIT_ERROR_CODES or subcode in META_RATE_LIMIT_ERROR_CODES:
        return "rate_limited", "RATE_LIMIT"
    if code in META_QUALITY_ERROR_CODES or subcode in META_QUALITY_ERROR_CODES:
        return "quality_restriction", "QUALITY"
    if status in {408}:
        return "timeout", "TRANSIENT"
    if status >= 500:
        return "provider_5xx", "TRANSIENT"
    return "meta_whatsapp_error", "PERMANENT"


async def _redis_rate_client() -> redis.Redis | None:
    global _REDIS_RATE_CLIENT
    if str(os.getenv("CAMPAIGN_RATE_LIMIT_REDIS", "1")).strip().lower() in {"0", "false", "no"}:
        return None
    if _REDIS_RATE_CLIENT is None:
        _REDIS_RATE_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    return _REDIS_RATE_CLIENT


async def acquire_campaign_send_rate_slot(
    *,
    phone_number_id: str,
    max_mps: int,
    now: datetime | None = None,
    redis_client: Any | None = None,
) -> bool:
    now = now or datetime.utcnow()
    bucket_second = int(now.timestamp())
    clean_phone = str(phone_number_id or "").strip() or "unknown"
    limit = max(1, int(max_mps or DEFAULT_AGENTOS_MPS))
    key = f"campaign:send-rate:{clean_phone}:{bucket_second}"
    client = redis_client
    if client is None:
        try:
            client = await _redis_rate_client()
        except Exception:
            client = None
    if client is not None:
        count = int(await client.incr(key))
        if count == 1:
            await client.expire(key, 3)
        return count <= limit
    async with _MEMORY_RATE_LOCK:
        count, seen_second = _MEMORY_RATE_BUCKETS.get(clean_phone, (0, bucket_second))
        if seen_second != bucket_second:
            count = 0
            seen_second = bucket_second
        count += 1
        _MEMORY_RATE_BUCKETS[clean_phone] = (count, seen_second)
        return count <= limit


async def list_central_campaigns(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(CampanhaDisparo)
            .where(CampanhaDisparo.empresa_id == empresa_id)
            .order_by(CampanhaDisparo.criado_em.desc())
            .limit(100)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "nome": row.nome,
            "status": str(row.central_status or (row.status.value if hasattr(row.status, "value") else row.status)),
            "legacy_status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "conexao_id": str(row.conexao_id) if row.conexao_id else None,
            "meta_template_id": str(row.meta_template_id) if row.meta_template_id else None,
            "created_at": row.criado_em.isoformat() if row.criado_em else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "queued_at": row.queued_at.isoformat() if row.queued_at else None,
            "ai_reply_enabled": True if row.ai_reply_enabled is None else bool(row.ai_reply_enabled),
            "paused_at": row.paused_at.isoformat() if row.paused_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
            "results": await campaign_results(session, empresa_id=empresa_id, campaign_id=row.id),
        }
        for row in rows
    ]


async def start_campaign_from_snapshot(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    campaign_id: uuid.UUID,
    snapshot_id: uuid.UUID | str | None = None,
    confirmed_recipient_count: int | str | None = None,
    started_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    confirmed_snapshot_id, confirmed_count = _parse_start_confirmation(
        snapshot_id=snapshot_id,
        confirmed_recipient_count=confirmed_recipient_count,
    )
    existing = (
        await session.execute(
            select(CampanhaDisparo).where(CampanhaDisparo.empresa_id == empresa_id, CampanhaDisparo.id == campaign_id)
        )
    ).scalars().first()
    if existing:
        existing_payload = dict(existing.payload_json or {})
        if str(existing_payload.get("snapshot_id") or "") != str(confirmed_snapshot_id):
            raise CampaignCentralError("Confirmação não corresponde ao snapshot iniciado.")
        if str(existing.central_status or "").strip().lower() not in CAMPAIGN_STARTABLE_STATUSES:
            raise CampaignCentralError("Campanha não está em estado iniciável.")
        return {
            "started": False,
            "idempotent": True,
            "campaign_id": str(existing.id),
            "status": str(existing.central_status or "queued"),
            "ai_reply_enabled": True if existing.ai_reply_enabled is None else bool(existing.ai_reply_enabled),
            "results": await campaign_results(session, empresa_id=empresa_id, campaign_id=existing.id),
        }

    snapshot = (
        await session.execute(
            text(
                """
                select *
                from campaign_preparation_snapshots
                where empresa_id = :empresa_id and campaign_id = :campaign_id and id = :snapshot_id
                order by created_at desc
                limit 1
                """
            ),
            {"empresa_id": empresa_id, "campaign_id": campaign_id, "snapshot_id": confirmed_snapshot_id},
        )
    ).mappings().first()
    if not snapshot:
        raise CampaignCentralError("Snapshot preparado não encontrado para esta empresa.")
    latest_snapshot_id = (
        await session.execute(
            text(
                """
                select id
                from campaign_preparation_snapshots
                where empresa_id = :empresa_id and campaign_id = :campaign_id
                order by created_at desc, id desc
                limit 1
                """
            ),
            {"empresa_id": empresa_id, "campaign_id": campaign_id},
        )
    ).scalar_one_or_none()
    if str(latest_snapshot_id or "") != str(confirmed_snapshot_id):
        raise CampaignCentralError("Snapshot preparado foi substituído; prepare a campanha novamente.")

    filters = dict(snapshot.get("filters_json") or {})
    sender = dict(filters.get("sender") or {})
    template_info = dict(filters.get("template") or {})
    variable_bindings = normalize_campaign_variable_bindings(filters.get("variable_bindings"))
    media = dict(filters.get("media") or {})
    inclusions = list(snapshot.get("inclusions_json") or [])
    if confirmed_count != len(inclusions):
        raise CampaignCentralError("Contagem confirmada diverge do snapshot preparado.")

    conexao_id = uuid.UUID(str(sender.get("conexao_id") or ""))
    template_id = uuid.UUID(str(template_info.get("id") or ""))
    connection = (
        await session.execute(select(Conexao).where(Conexao.empresa_id == empresa_id, Conexao.id == conexao_id))
    ).scalars().first()
    template = (
        await session.execute(
            select(MetaMessageTemplate)
            .where(
                MetaMessageTemplate.empresa_id == empresa_id,
                MetaMessageTemplate.id == template_id,
                MetaMessageTemplate.conexao_id == conexao_id,
            )
            .options(selectinload(MetaMessageTemplate.components))
        )
    ).scalars().first()
    if not connection or not template:
        raise CampaignCentralError("Conexão/template do snapshot não pertencem à empresa.")
    if not _is_meta_official(connection):
        raise CampaignCentralError("Campanha Central real exige conexão WhatsApp Meta Official.")
    if str(template.normalized_status or "").lower() != "approved":
        raise CampaignCentralError("Campanha real exige template Meta aprovado.")
    if str(template.waba_id or "") != str(sender.get("waba_id") or ""):
        raise CampaignCentralError("Template não pertence à WABA preparada.")
    if template_requires_header_image([component.raw_component_json for component in template.components]) and not str(media.get("media_id") or ""):
        raise CampaignCentralError("Template com imagem exige media_id preparado.")

    components = [component.raw_component_json for component in template.components]
    parsed_template = parse_template_components(components)

    now = datetime.utcnow()
    campaign = CampanhaDisparo(
        id=campaign_id,
        empresa_id=empresa_id,
        nome=str(filters.get("name") or f"Campanha {str(campaign_id)[:8]}"),
        status=CampanhaDisparoStatus.PENDENTE,
        central_status="queued",
        conexao_id=conexao_id,
        meta_template_id=template_id,
        queued_at=now,
        started_at=now,
        ai_reply_enabled=bool(filters.get("ai_reply_enabled")),
        payload_json={
            "source": "campaign_central",
            "snapshot_id": str(snapshot["id"]),
            "variable_bindings": sanitize_for_log(variable_bindings),
            "sender": sanitize_for_log(sender),
            "media": sanitize_for_log(media),
            "started_by": str(started_by) if started_by else None,
            "send_mode": campaign_send_mode(connection),
            "allowlist_active": campaign_allowlist_enforced(connection),
            "ai_reply_enabled": bool(filters.get("ai_reply_enabled")),
        },
    )
    session.add(campaign)
    await session.flush()

    selected = 0
    blocked = 0
    seen_addresses: set[str] = set()
    for item in inclusions:
        if not isinstance(item, dict) or not item.get("lead_id"):
            continue
        lead = (
            await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.id == uuid.UUID(str(item["lead_id"]))))
        ).scalars().first()
        if not lead:
            continue
        address, phone_ok = normalize_phone(str(lead.telefone_contato or ""), default_country_code=filters.get("default_country_code"))
        if not address or not phone_ok:
            continue
        if address in seen_addresses:
            continue
        seen_addresses.add(address)
        recipient_parameters, missing_parameters = resolve_campaign_template_parameters_for_lead(
            parsed_template=parsed_template,
            bindings=variable_bindings,
            lead=lead,
            media=media,
        )
        if missing_parameters:
            recipient_key = deterministic_idempotency_key("campaign_recipient", empresa_id, campaign_id, address)
            recipient = CampaignRecipient(
                empresa_id=empresa_id,
                campanha_id=campaign_id,
                conexao_id=conexao_id,
                provider="meta_whatsapp",
                channel="whatsapp",
                lead_id=lead.id,
                recipient_address=address,
                meta_template_id=template_id,
                parameters_json=sanitize_for_log({"missing": missing_parameters}),
                payload_json={"error": "template_variable_missing"},
                status="blocked",
                error_code="TEMPLATE_VARIABLE_MISSING",
                error_detail=";".join(missing_parameters)[:500],
                idempotency_key=recipient_key,
                scheduled_at=now,
            )
            session.add(recipient)
            blocked += 1
            continue
        recipient_template_payload = build_meta_template_send_payload(
            template_name=template.name,
            language=template.language,
            parsed_template=parsed_template,
            parameters=recipient_parameters,
        )
        history_rendering = render_campaign_history_content(components, parsed_template, recipient_parameters)
        operational = await ensure_operational_conversation(
            session,
            empresa_id=empresa_id,
            conexao_id=conexao_id,
            external_contact_id=address,
            lead=lead,
            direction="outbound",
            message_at=now,
            metadata={
                "source": "campaign_central_start",
                "campaign_id": str(campaign_id),
                "campaign_outbound": True,
            },
        )
        initialize_conversation_ai_from_campaign(
            lead,
            enabled=bool(campaign.ai_reply_enabled),
            campaign_id=str(campaign.id),
            created_new_conversation=bool(
                (operational.conversation.metadata_json or {}).get("campaign_created_new_conversation")
            ),
        )
        await session.flush()
        allowed, reason = await communication_allowed(
            session,
            empresa_id=empresa_id,
            contact_id=operational.contact.id,
            channel="whatsapp",
            provider="meta_whatsapp",
            conexao_id=conexao_id,
        )
        recipient_key = deterministic_idempotency_key("campaign_recipient", empresa_id, campaign_id, address)
        recipient = (
            await session.execute(
                select(CampaignRecipient).where(
                    CampaignRecipient.empresa_id == empresa_id,
                    CampaignRecipient.idempotency_key == recipient_key,
                )
            )
        ).scalars().first()
        if not recipient:
            recipient = CampaignRecipient(
                empresa_id=empresa_id,
                campanha_id=campaign_id,
                conexao_id=conexao_id,
                provider="meta_whatsapp",
                channel="whatsapp",
                lead_id=lead.id,
                contact_id=operational.contact.id,
                conversation_id=operational.conversation.id,
                recipient_address=address,
                meta_template_id=template_id,
                parameters_json=sanitize_for_log(recipient_parameters),
                payload_json={
                    "template": recipient_template_payload,
                    "media": sanitize_for_log(media),
                    "history_rendering": history_rendering,
                },
                status="queued" if allowed else "blocked",
                error_code=None if allowed else "communication_consent_block",
                error_detail=None if allowed else reason,
                idempotency_key=recipient_key,
                scheduled_at=now,
            )
            session.add(recipient)
            await session.flush()
        if recipient.status == "blocked":
            blocked += 1
            continue
        outbox_key = deterministic_idempotency_key("campaign_outbox", empresa_id, campaign_id, recipient.id)
        await enqueue_message(
            session,
            empresa_id=empresa_id,
            conexao_id=conexao_id,
            provider="meta_whatsapp",
            channel="whatsapp",
            operation_type="campaign_template_send",
            payload={
                "campaign_id": str(campaign_id),
                "campaign_recipient_id": str(recipient.id),
                "meta_template_id": str(template_id),
                "template_payload": recipient_template_payload,
                "media": sanitize_for_log(media),
                "history_rendering": history_rendering,
            },
            idempotency_key=outbox_key,
            campaign_recipient_id=recipient.id,
            max_attempts=MAX_SEND_ATTEMPTS,
        )
        selected += 1

    await _refresh_campaign_completion(session, empresa_id=empresa_id, campaign_id=campaign_id)
    return {
        "started": True,
        "idempotent": False,
        "campaign_id": str(campaign_id),
        "status": campaign.central_status,
        "materialized": {"queued": selected, "blocked": blocked, "deduped": len(inclusions) - selected - blocked},
        "send_mode": campaign_send_mode(connection),
        "allowlist_active": campaign_allowlist_enforced(connection),
        "results": await campaign_results(session, empresa_id=empresa_id, campaign_id=campaign_id),
    }


def _rate(numerator: int, denominator: int) -> float:
    return round((int(numerator) / int(denominator)) * 100, 2) if int(denominator or 0) else 0


def _results_filters(
    *,
    campaign_id: uuid.UUID | None = None,
    conexao_id: uuid.UUID | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = ["r.empresa_id = :empresa_id"]
    params: dict[str, Any] = {}
    if campaign_id:
        clauses.append("r.campanha_id = :campaign_id")
        params["campaign_id"] = str(campaign_id)
    if conexao_id:
        clauses.append("r.conexao_id = :conexao_id")
        params["conexao_id"] = str(conexao_id)
    if period_start:
        clauses.append("coalesce(r.sent_at, r.created_at) >= :period_start")
        params["period_start"] = period_start
    if period_end:
        clauses.append("coalesce(r.sent_at, r.created_at) < :period_end")
        params["period_end"] = period_end
    return " and ".join(clauses), params


async def campaign_results_analytics(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    conexao_id: uuid.UUID | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    include_timeline: bool = True,
    include_segments: bool = False,
    recipient_limit: int | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if recipient_limit is not None and not 1 <= int(recipient_limit) <= 10_000:
        raise ValueError("recipient_limit must be between 1 and 10000")
    where_sql, extra_params = _results_filters(
        campaign_id=campaign_id,
        conexao_id=conexao_id,
        period_start=period_start,
        period_end=period_end,
    )
    params = {"empresa_id": str(empresa_id), **extra_params}
    as_of_clause = ""
    status_as_of_clause = ""
    attempt_as_of_clause = ""
    status_scope_clause = ""
    attempt_scope_clause = ""
    status_event_timestamp = "ev.occurred_at"
    accepted_fallback = "sr.event_sent_at, ar.attempt_accepted_at"
    status_acceptance_clause = "or status in ('sent', 'delivered', 'read')"
    status_delivery_clause = "or status in ('delivered', 'read')"
    recipient_status_expr = "r.status"
    if observed_at is not None:
        # Campaign timestamp columns are stored as naive UTC. Convert callers' UTC
        # instants at the boundary so comparisons do not depend on DB timezone.
        observed_at = observed_at.replace(tzinfo=None) if observed_at.tzinfo is None else observed_at.astimezone(timezone.utc).replace(tzinfo=None)
        params["observed_at"] = observed_at
        as_of_clause = " and coalesce(r.sent_at, r.created_at) <= :observed_at"
        status_as_of_clause = " and coalesce(ev.occurred_at, ev.created_at) <= :observed_at"
        attempt_as_of_clause = " and ma.finished_at <= :observed_at"
        status_scope_clause = " and ev.conexao_id = r.conexao_id and ev.channel = r.channel"
        attempt_scope_clause = " and mo.provider = r.provider and mo.conexao_id = r.conexao_id and mo.channel = r.channel and ma.provider = r.provider and ma.conexao_id = r.conexao_id"
        status_event_timestamp = "coalesce(ev.occurred_at, ev.created_at)"
        accepted_fallback = "sr.event_sent_at, sr.event_delivered_at, sr.event_read_at, ar.attempt_accepted_at"
        recipient_status_expr = """case when r.updated_at <= :observed_at then r.status else coalesce((
            select state.status from (values
                (r.sent_at, 'sent', 1), (r.delivered_at, 'delivered', 2),
                (r.read_at, 'read', 3), (r.failed_at, 'failed', 4), (r.cancelled_at, 'cancelled', 5)
            ) as state(status_at, status, priority)
            where state.status_at is not null and state.status_at <= :observed_at
            order by state.status_at desc, state.priority desc limit 1
        ), 'unknown') end"""
        status_acceptance_clause = "or observed_status in ('sent', 'delivered', 'read')"
        status_delivery_clause = "or observed_status in ('delivered', 'read')"
    recipient_sent_at = "r.sent_at" if observed_at is None else "case when r.sent_at <= :observed_at then r.sent_at end"
    recipient_delivered_at = "r.delivered_at" if observed_at is None else "case when r.delivered_at <= :observed_at then r.delivered_at end"
    recipient_read_at = "r.read_at" if observed_at is None else "case when r.read_at <= :observed_at then r.read_at end"
    recipient_failed_at = "r.failed_at" if observed_at is None else "case when r.failed_at <= :observed_at then r.failed_at end"
    status_updated_clause = "true" if observed_at is None else "updated_at <= :observed_at"
    reply_as_of_clause = "" if observed_at is None else " and mh.criado_em <= :observed_at"
    if recipient_limit is not None:
        params["recipient_limit"] = int(recipient_limit)
    scoped_order_limit = (
        " order by coalesce(r.sent_at, r.created_at), r.id limit :recipient_limit"
        if recipient_limit is not None else ""
    )
    origin_cte = """
        origin_rollup as (
            select r.id as recipient_id, coalesce(origin.event_kind, 'unknown') as origin_segment
            from scoped_recipients r
            left join lateral (
                select t.event_kind
                from attribution_touches t
                where t.empresa_id = r.empresa_id
                  and t.event_kind in ('ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source')
                  and t.occurred_at <= coalesce(r.sent_at, r.created_at) at time zone 'UTC'
                  and ((r.lead_id is not null and t.lead_id = r.lead_id)
                    or (r.lead_id is null and r.contact_id is not null and t.contact_id = r.contact_id)
                    or (r.lead_id is null and r.contact_id is null and r.conversation_id is not null and t.conversation_id = r.conversation_id))
                order by t.occurred_at asc, t.id asc
                limit 1
            ) origin on true
        ),
    """ if include_segments else ""
    origin_join = " left join origin_rollup origin on origin.recipient_id = r.id " if include_segments else ""
    origin_select = ", coalesce(origin.origin_segment, 'unknown') as origin_segment" if include_segments else ""
    segments_select = """
            , (select coalesce(jsonb_agg(to_jsonb(segment_row) order by segment_row.segment), '[]'::jsonb)
               from (
                    select c.origin_segment as segment,
                           count(*) as audience_total,
                           count(*) filter (where c.is_meta_accepted) as meta_accepted,
                           count(*) filter (where c.is_delivered) as delivered,
                           count(*) filter (where c.is_read) as read,
                           count(*) filter (where c.is_failed) as failed,
                           count(*) filter (where c.is_pending and not c.is_meta_accepted and not c.is_failed) as pending,
                           count(replies.recipient_id) as replies
                    from classified c
                    left join replies on replies.recipient_id = c.id
                    group by c.origin_segment
               ) segment_row) as segments
    """ if include_segments else ""
    metrics_sql = text(f"""
        with scoped_recipients as (
            select r.*
            from campaign_recipients r
            where {where_sql}{as_of_clause}
            {scoped_order_limit}
        ),
        status_rollup as (
            select
                r.id as recipient_id,
                bool_or(ev.status = 'sent') as event_sent,
                bool_or(ev.status = 'delivered') as event_delivered,
                bool_or(ev.status = 'read') as event_read,
                bool_or(ev.status = 'failed') as event_failed,
                min({status_event_timestamp}) filter (where ev.status = 'sent') as event_sent_at,
                min({status_event_timestamp}) filter (where ev.status = 'delivered') as event_delivered_at,
                min({status_event_timestamp}) filter (where ev.status = 'read') as event_read_at,
                min({status_event_timestamp}) filter (where ev.status = 'failed') as event_failed_at
            from scoped_recipients r
            left join message_status_events ev
              on ev.empresa_id = r.empresa_id
             and ev.provider = r.provider
             and ev.external_message_id = r.external_message_id
             {status_scope_clause}
             {status_as_of_clause}
            group by r.id
        ),
        attempt_rollup as (
            select
                r.id as recipient_id,
                bool_or(ma.result_status in ('accepted', 'sent', 'completed') and ma.external_message_id is not null) as attempt_accepted,
                min(ma.finished_at) filter (where ma.result_status in ('accepted', 'sent', 'completed') and ma.external_message_id is not null) as attempt_accepted_at,
                bool_or(ma.result_status = 'failed') as attempt_failed
            from scoped_recipients r
            left join message_outbox mo
              on mo.empresa_id = r.empresa_id
             and mo.campaign_recipient_id = r.id
            left join message_attempts ma
              on ma.empresa_id = mo.empresa_id
             and ma.outbox_id = mo.id
             {attempt_scope_clause}
             {attempt_as_of_clause}
            group by r.id
        ),
        {origin_cte}
        recipient_facts as (
            select
                r.*,
                {recipient_status_expr} as observed_status,
                coalesce(sr.event_sent, false) as event_sent,
                coalesce(sr.event_delivered, false) as event_delivered,
                coalesce(sr.event_read, false) as event_read,
                coalesce(sr.event_failed, false) as event_failed,
                coalesce(ar.attempt_accepted, false) as attempt_accepted,
                coalesce(ar.attempt_failed, false) as attempt_failed,
                coalesce({recipient_sent_at}, {accepted_fallback}) as accepted_at,
                coalesce({recipient_delivered_at},
                         sr.event_delivered_at,
                         {recipient_read_at},
                         sr.event_read_at) as delivered_stage_at,
                coalesce({recipient_read_at},
                         sr.event_read_at) as read_stage_at,
                coalesce({recipient_failed_at},
                         sr.event_failed_at) as failed_stage_at
                {origin_select}
            from scoped_recipients r
            left join status_rollup sr on sr.recipient_id = r.id
            left join attempt_rollup ar on ar.recipient_id = r.id
            {origin_join}
        ),
        classified as (
            select
                *,
                (
                    (external_message_id is not null and {status_updated_clause})
                    or accepted_at is not null
                    {status_acceptance_clause}
                    or attempt_accepted
                    or event_sent
                    or event_delivered
                    or event_read
                ) as is_meta_accepted,
                (
                    delivered_stage_at is not null
                    {status_delivery_clause}
                    or event_delivered
                    or event_read
                ) as is_delivered,
                (
                    read_stage_at is not null
                    or (observed_status = 'read' and {status_updated_clause})
                    or event_read
                ) as is_read,
                (
                    (failed_stage_at is not null or (observed_status in ('failed', 'blocked') and {status_updated_clause}) or event_failed)
                    and not (
                        delivered_stage_at is not null
                        or read_stage_at is not null
                        {status_delivery_clause}
                        or event_delivered
                        or event_read
                    )
                ) as is_failed,
                observed_status in ('queued', 'locked', 'retry', 'processing') as is_pending
            from recipient_facts
        ),
        replies as (
            select distinct c.id as recipient_id
            from classified c
            join mensagens_historico mh
              on mh.empresa_id = c.empresa_id
             and mh.channel_conversation_id = c.conversation_id
             and coalesce(mh.from_me, false) = false
             and coalesce(mh.criado_em, now()) > coalesce(c.accepted_at, c.sent_at, c.created_at)
             {reply_as_of_clause}
            where c.conversation_id is not null and c.is_meta_accepted
        )
        select
            count(*) as audience_total,
            count(*) filter (where is_meta_accepted) as meta_accepted,
            count(*) filter (where is_delivered) as delivered,
            count(*) filter (where is_read) as read,
            count(*) filter (where is_failed) as failed,
            count(*) filter (where is_pending and not is_meta_accepted and not is_failed) as pending,
            count(replies.recipient_id) as replies
            {segments_select}
        from classified
        left join replies on replies.recipient_id = classified.id
    """)
    row = (await session.execute(metrics_sql, params)).mappings().first() or {}
    metrics = {key: int(row.get(key) or 0) for key in ("audience_total", "meta_accepted", "delivered", "read", "failed", "pending", "replies")}
    segments = row.get("segments") or []
    capped = recipient_limit is not None and metrics["audience_total"] >= recipient_limit

    old_rows = (
        await session.execute(
            text(f"""
                select r.status, count(*) as count
                from campaign_recipients r
                where {where_sql}{as_of_clause}
                group by r.status
            """),
            params,
        )
    ).all() if include_timeline else []
    old_status_counts = {str(status): int(count) for status, count in old_rows}
    for key in ("queued", "locked", "retry", "sent", "delivered", "read", "failed", "blocked", "cancelled", "stopped"):
        old_status_counts.setdefault(key, 0)

    minmax = (
        await session.execute(
            text(f"select min(coalesce(r.sent_at, r.created_at)) as start_at, max(coalesce(r.read_at, r.delivered_at, r.sent_at, r.failed_at, r.created_at)) as end_at from campaign_recipients r where {where_sql}{as_of_clause}"),
            params,
        )
    ).mappings().first() or {} if include_timeline else {}
    start_at = minmax.get("start_at")
    end_at = minmax.get("end_at")
    bucket = "hour" if start_at and end_at and (end_at - start_at) <= timedelta(days=2) else "day"
    timeline_rows = (
        await session.execute(
            text(f"""
                with scoped_recipients as (
                    select r.*
                    from campaign_recipients r
                    where {where_sql}{as_of_clause}
                ),
                status_rollup as (
                    select
                        r.id as recipient_id,
                min({status_event_timestamp}) filter (where ev.status = 'sent') as event_sent_at,
                min({status_event_timestamp}) filter (where ev.status = 'delivered') as event_delivered_at,
                min({status_event_timestamp}) filter (where ev.status = 'read') as event_read_at,
                min({status_event_timestamp}) filter (where ev.status = 'failed') as event_failed_at
                    from scoped_recipients r
                    left join message_status_events ev
                      on ev.empresa_id = r.empresa_id
                     and ev.provider = r.provider
                     and ev.external_message_id = r.external_message_id
                     {status_scope_clause}
                     {status_as_of_clause}
                    group by r.id
                ),
                attempt_rollup as (
                    select
                        r.id as recipient_id,
                        min(ma.finished_at) filter (where ma.result_status in ('accepted', 'sent', 'completed') and ma.external_message_id is not null) as attempt_accepted_at
                    from scoped_recipients r
                    left join message_outbox mo
                      on mo.empresa_id = r.empresa_id
                     and mo.campaign_recipient_id = r.id
                    left join message_attempts ma
                      on ma.empresa_id = mo.empresa_id
                     and ma.outbox_id = mo.id
                    group by r.id
                ),
                events as (
                    select date_trunc(:bucket, coalesce(r.sent_at, sr.event_sent_at, ar.attempt_accepted_at)) as bucket_at, 'meta_accepted' as metric, count(*) as value
                    from scoped_recipients r
                    left join status_rollup sr on sr.recipient_id = r.id
                    left join attempt_rollup ar on ar.recipient_id = r.id
                    where coalesce(r.sent_at, sr.event_sent_at, ar.attempt_accepted_at) is not null or r.external_message_id is not null or r.status in ('sent', 'delivered', 'read')
                    group by 1
                    union all
                    select date_trunc(:bucket, coalesce(r.delivered_at, sr.event_delivered_at, r.read_at, sr.event_read_at)) as bucket_at, 'delivered' as metric, count(*) as value
                    from scoped_recipients r
                    left join status_rollup sr on sr.recipient_id = r.id
                    where coalesce(r.delivered_at, sr.event_delivered_at, r.read_at, sr.event_read_at) is not null or r.status in ('delivered', 'read')
                    group by 1
                    union all
                    select date_trunc(:bucket, coalesce(r.read_at, sr.event_read_at)) as bucket_at, 'read' as metric, count(*) as value
                    from scoped_recipients r
                    left join status_rollup sr on sr.recipient_id = r.id
                    where coalesce(r.read_at, sr.event_read_at) is not null or r.status = 'read'
                    group by 1
                    union all
                    select date_trunc(:bucket, coalesce(r.failed_at, sr.event_failed_at)) as bucket_at, 'failed' as metric, count(*) as value
                    from scoped_recipients r
                    left join status_rollup sr on sr.recipient_id = r.id
                    where coalesce(r.failed_at, sr.event_failed_at) is not null or r.status in ('failed', 'blocked')
                    group by 1
                )
                select bucket_at, metric, value from events where bucket_at is not null order by bucket_at asc, metric asc
            """),
            {**params, "bucket": bucket},
        )
    ).mappings().all() if include_timeline else []
    timeline_map: dict[str, dict[str, Any]] = {}
    for item in timeline_rows:
        bucket_at = item.get("bucket_at")
        key = bucket_at.isoformat() if hasattr(bucket_at, "isoformat") else str(bucket_at)
        timeline_map.setdefault(key, {"bucket": key, "meta_accepted": 0, "delivered": 0, "read": 0, "failed": 0})
        timeline_map[key][str(item.get("metric"))] = int(item.get("value") or 0)

    campaign_label = "Todas as campanhas"
    if campaign_id:
        campaign = await _campaign_or_error(session, empresa_id=empresa_id, campaign_id=campaign_id)
        campaign_label = campaign.nome
    return {
        "scope": {
            "type": "campaign" if campaign_id else "company",
            "label": campaign_label,
            "campaign_id": str(campaign_id) if campaign_id else None,
            "conexao_id": str(conexao_id) if conexao_id else None,
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": period_end.isoformat() if period_end else None,
        },
        "metrics": metrics,
        "segments": segments,
        "coverage": {
            "state": "partial" if capped else ("observed" if metrics["audience_total"] else "unknown"),
            "sampled_recipients": metrics["audience_total"],
            "sample_limit": recipient_limit,
            "capped": capped,
            "origin_segment_source": "earliest persisted attribution touch before campaign dispatch; unknown when absent",
        },
        "rates": {
            "delivery_rate": _rate(metrics["delivered"], metrics["meta_accepted"]),
            "read_rate": _rate(metrics["read"], metrics["delivered"]),
            "reply_rate": _rate(metrics["replies"], metrics["delivered"]),
        },
        "percentages": {
            "meta_accepted": _rate(metrics["meta_accepted"], metrics["audience_total"]),
            "delivered": _rate(metrics["delivered"], metrics["meta_accepted"]),
            "read": _rate(metrics["read"], metrics["delivered"]),
            "replies": _rate(metrics["replies"], metrics["delivered"]),
            "failed": _rate(metrics["failed"], metrics["audience_total"]),
        },
        "old_status_counts": old_status_counts,
        "timeline": {
            "granularity": bucket,
            "series": list(timeline_map.values()),
        },
        "funnel": [
            {"key": "audience_total", "label": "Público", "value": metrics["audience_total"], "previous_rate": None},
            {"key": "meta_accepted", "label": "Aceitas pela Meta", "value": metrics["meta_accepted"], "previous_rate": _rate(metrics["meta_accepted"], metrics["audience_total"])},
            {"key": "delivered", "label": "Entregues", "value": metrics["delivered"], "previous_rate": _rate(metrics["delivered"], metrics["meta_accepted"])},
            {"key": "read", "label": "Lidas", "value": metrics["read"], "previous_rate": _rate(metrics["read"], metrics["delivered"])},
            {"key": "replies", "label": "Respostas", "value": metrics["replies"], "previous_rate": _rate(metrics["replies"], metrics["delivered"])},
        ],
        "costs": {"state": "unavailable", "message": "Nao disponivel sem fonte auditavel de precificacao/billable."},
        "conversions": {"state": "no_data", "message": "Sem eventos de conversao registrados."},
        "definitions": {
            "audience_total": "Destinatarios unicos materializados da campanha/snapshot no escopo.",
            "meta_accepted": "Destinatarios com WAMID/external_message_id, tentativa aceita ou evidencia sent/delivered/read.",
            "delivered": "Destinatarios que atingiram delivered ou read.",
            "read": "Destinatarios que atingiram read.",
            "failed": "Destinatarios com falha final real ou bloqueio de provider/politica, sem evidencia posterior de delivered/read.",
            "pending": "Destinatarios ainda em queued/locked/retry/processing sem aceite/falha final.",
            "replies": "Destinatarios com inbound na mesma conversa depois do outbound aceito da campanha.",
        },
    }


async def campaign_results(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, Any]:
    return await campaign_results_analytics(session, empresa_id=empresa_id, campaign_id=campaign_id)


async def pause_campaign(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, Any]:
    campaign = await _campaign_or_error(session, empresa_id=empresa_id, campaign_id=campaign_id)
    if campaign.central_status in {"completed", "cancelled"}:
        raise CampaignCentralError("Campanha terminal não pode ser pausada.")
    campaign.central_status = "paused"
    campaign.paused_at = datetime.utcnow()
    return {"campaign_id": str(campaign.id), "status": campaign.central_status}


async def resume_campaign(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, Any]:
    campaign = await _campaign_or_error(session, empresa_id=empresa_id, campaign_id=campaign_id)
    if campaign.central_status != "paused":
        raise CampaignCentralError("Somente campanha pausada pode ser retomada.")
    campaign.central_status = "queued"
    campaign.resumed_at = datetime.utcnow()
    return {"campaign_id": str(campaign.id), "status": campaign.central_status}


async def cancel_campaign(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, Any]:
    campaign = await _campaign_or_error(session, empresa_id=empresa_id, campaign_id=campaign_id)
    now = datetime.utcnow()
    campaign.central_status = "cancelled"
    campaign.cancelled_at = campaign.cancelled_at or now
    recipients = (
        await session.execute(
            select(CampaignRecipient).where(
                CampaignRecipient.empresa_id == empresa_id,
                CampaignRecipient.campanha_id == campaign_id,
                CampaignRecipient.status.in_(["queued", "retry", "locked"]),
            )
        )
    ).scalars().all()
    for recipient in recipients:
        recipient.status = "cancelled"
        recipient.cancelled_at = recipient.cancelled_at or now
    outboxes = (
        await session.execute(
            select(MessageOutbox)
            .join(CampaignRecipient, CampaignRecipient.id == MessageOutbox.campaign_recipient_id)
            .where(
                MessageOutbox.empresa_id == empresa_id,
                CampaignRecipient.campanha_id == campaign_id,
                MessageOutbox.status.in_(["queued", "retry", "locked"]),
            )
        )
    ).scalars().all()
    for outbox in outboxes:
        outbox.status = "cancelled"
        outbox.cancelled_at = outbox.cancelled_at or now
        outbox.locked_at = None
        outbox.locked_by = None
    return {"campaign_id": str(campaign.id), "status": campaign.central_status, "cancelled_recipients": len(recipients)}


async def _campaign_or_error(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> CampanhaDisparo:
    campaign = (
        await session.execute(
            select(CampanhaDisparo).where(CampanhaDisparo.empresa_id == empresa_id, CampanhaDisparo.id == campaign_id)
        )
    ).scalars().first()
    if not campaign:
        raise CampaignCentralError("Campanha não encontrada para esta empresa.")
    return campaign


async def claim_campaign_outbox_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    limit: int,
    now: datetime | None = None,
    lock_timeout_seconds: int = 300,
) -> list[MessageOutbox]:
    now = now or datetime.utcnow()
    stale_before = now - timedelta(seconds=max(30, int(lock_timeout_seconds)))
    stmt = (
        select(MessageOutbox)
        .join(CampaignRecipient, CampaignRecipient.id == MessageOutbox.campaign_recipient_id)
        .join(CampanhaDisparo, CampanhaDisparo.id == CampaignRecipient.campanha_id)
        .where(
            MessageOutbox.operation_type == "campaign_template_send",
            MessageOutbox.status.in_(["queued", "retry"]),
            MessageOutbox.available_at <= now,
            (MessageOutbox.locked_at.is_(None)) | (MessageOutbox.locked_at < stale_before),
            CampaignRecipient.status.in_(["queued", "retry"]),
            CampanhaDisparo.central_status.in_(["queued", "running"]),
        )
        .order_by(MessageOutbox.priority.asc(), MessageOutbox.available_at.asc(), MessageOutbox.created_at.asc())
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        row.status = "locked"
        row.locked_at = now
        row.locked_by = str(worker_id or "")[:120]
    return rows


async def process_campaign_outbox_once(*, worker_id: str | None = None, limit: int | None = None) -> dict[str, int]:
    worker = worker_id or f"campaign-worker-{os.getpid()}"
    processed = sent = failed = blocked = skipped = retried = 0
    async with AsyncSessionLocal() as session:
        rows = await claim_campaign_outbox_batch(session, worker_id=worker, limit=limit or campaign_worker_batch_size())
        await session.commit()
    if rows:
        worker_logger.info(
            "WORKER_JOB_CLAIM worker=campaign claimed=%s claim_mode=for_update_skip_locked",
            len(rows),
        )
    for outbox in rows:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(MessageOutbox)
                    .where(MessageOutbox.id == outbox.id)
                    .options(selectinload(MessageOutbox.campaign_recipient).selectinload(CampaignRecipient.campanha))
                )
            ).scalars().first()
            if not row or not row.campaign_recipient:
                skipped += 1
                continue
            result = await _send_campaign_outbox(session, outbox=row)
            await session.commit()
            if result == "sent" and row.external_message_id:
                await broadcast_persisted_campaign_history(
                    empresa_id=row.empresa_id,
                    provider=row.provider,
                    external_message_id=row.external_message_id,
                )
            processed += 1
            sent += int(result == "sent")
            failed += int(result == "failed")
            blocked += int(result == "blocked")
            if result == "sent":
                worker_logger.info("WORKER_JOB_SUCCESS worker=campaign outcome=sent")
            elif result == "retry":
                retried += 1
                worker_logger.warning("WORKER_JOB_RETRY worker=campaign outcome=retry")
            elif result in {"failed", "blocked"}:
                worker_logger.error("WORKER_JOB_FAILED worker=campaign outcome=%s", result)
    return {
        "processed": processed,
        "sent": sent,
        "failed": failed,
        "blocked": blocked,
        "skipped": skipped,
        "retried": retried,
    }


async def broadcast_persisted_campaign_history(*, empresa_id: uuid.UUID, provider: str, external_message_id: str) -> bool:
    """Broadcast the committed history row through the existing Inbox event."""
    async with AsyncSessionLocal() as session:
        history = (
            await session.execute(
                select(MensagemHistorico).where(
                    MensagemHistorico.empresa_id == empresa_id,
                    MensagemHistorico.provider == provider,
                    MensagemHistorico.external_message_id == external_message_id,
                    MensagemHistorico.origem_mensagem == "campaign",
                ).order_by(MensagemHistorico.criado_em.desc()).limit(1)
            )
        ).scalars().first()
        if not history:
            return False
        payload = {
            "id": str(history.id),
            "lead_id": str(history.lead_id) if history.lead_id else None,
            "texto": history.texto or "",
            "from_me": bool(history.from_me),
            "direction": history.direction,
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
        await manager.broadcast_to_empresa(str(empresa_id), {
            "type": "inbox.message.created",
            "tipo_evento": "nova_mensagem_outbound",
            "empresa_id": str(empresa_id),
            "lead_id": str(history.lead_id) if history.lead_id else None,
            "conversation_id": str(history.channel_conversation_id) if history.channel_conversation_id else None,
            "direction": "outbound",
            "created_at": serialize_utc_timestamp(history.criado_em),
            "message": payload,
            "mensagem": payload,
            "metadata": {"provider": history.provider, "channel": history.channel, "origin": "campaign"},
        })
        return True


async def _materialize_campaign_history(
    session: AsyncSession,
    *,
    outbox: MessageOutbox,
    recipient: CampaignRecipient,
    external_message_id: str,
    template_payload: dict[str, Any],
    history_rendering: dict[str, Any] | None = None,
) -> MensagemHistorico:
    """Link a Graph-accepted campaign message to the Inbox history once."""
    # Serialize materialization for one recipient.  There is no database
    # uniqueness constraint on MensagemHistorico.external_message_id, so the
    # recipient lock is the application-level idempotency guard for retries.
    locked_recipient = (
        await session.execute(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.empresa_id == recipient.empresa_id,
                CampaignRecipient.id == recipient.id,
            )
            .with_for_update()
        )
    ).scalars().first()
    if locked_recipient is not None:
        recipient = locked_recipient

    existing = (
        await session.execute(
            select(MensagemHistorico).where(
                MensagemHistorico.empresa_id == recipient.empresa_id,
                MensagemHistorico.provider == recipient.provider,
                MensagemHistorico.external_message_id == external_message_id,
            ).limit(1)
        )
    ).scalars().first()
    if existing:
        return existing

    operational_conversation = None
    if not recipient.conversation_id:
        lead = (
            await session.execute(select(CRMLead).where(CRMLead.empresa_id == recipient.empresa_id, CRMLead.id == recipient.lead_id))
        ).scalars().first()
        operational_conversation = await ensure_operational_conversation(
            session,
            empresa_id=recipient.empresa_id,
            conexao_id=recipient.conexao_id,
            external_contact_id=recipient.recipient_address,
            lead=lead,
            direction="outbound",
            message_at=recipient.sent_at or datetime.utcnow(),
            metadata={
                "source": "campaign_history_materialization",
                "campaign_id": str(recipient.campanha_id),
                "campaign_recipient_id": str(recipient.id),
                "campaign_outbound": True,
            },
        )
        recipient.contact_id = operational_conversation.contact.id
        recipient.conversation_id = operational_conversation.conversation.id

    conversation_external_id = None
    if recipient.conversation_id:
        conversation = (
            await session.execute(
                select(ChannelConversation).where(
                    ChannelConversation.empresa_id == recipient.empresa_id,
                    ChannelConversation.id == recipient.conversation_id,
                )
            )
        ).scalars().first()
        conversation_external_id = getattr(conversation, "external_conversation_id", None)
    if operational_conversation is not None:
        conversation_external_id = operational_conversation.conversation.external_conversation_id

    template_name = str(template_payload.get("name") or "").strip()
    rendering = history_rendering if isinstance(history_rendering, dict) else {}
    history_body = str(rendering.get("body") or "").strip()
    history = MensagemHistorico(
        empresa_id=recipient.empresa_id,
        lead_id=recipient.lead_id,
        conexao_id=recipient.conexao_id,
        channel_conversation_id=recipient.conversation_id,
        channel=recipient.channel,
        provider=recipient.provider,
        external_message_id=external_message_id,
        external_conversation_id=conversation_external_id,
        direction="outbound",
        message_status="sent",
        provider_status="sent",
        texto=history_body or template_name or "Mensagem de template",
        tipo_mensagem="template",
        origem_mensagem="campaign",
        from_me=True,
        metadata_json={
            "origin": "campaign",
            "campaign_id": str(recipient.campanha_id),
            "campaign_recipient_id": str(recipient.id),
            "outbox_id": str(outbox.id),
            "external_message_id": external_message_id,
            "template_name": template_name or None,
            "template_history": rendering,
        },
    )
    session.add(history)
    await session.flush()
    status_events = (
        await session.execute(
            select(MessageStatusEvent).where(
                MessageStatusEvent.empresa_id == recipient.empresa_id,
                MessageStatusEvent.provider == recipient.provider,
                MessageStatusEvent.external_message_id == external_message_id,
            )
        )
    ).scalars().all()
    if status_events:
        from app.services.message_status_service import normalize_delivery_status, STATUS_RANK

        latest = max(
            status_events,
            key=lambda item: STATUS_RANK.get(normalize_delivery_status(item.status), 0),
        )
        history.message_status = normalize_delivery_status(latest.status)
        history.provider_status = history.message_status
    return history


async def _send_campaign_outbox(session: AsyncSession, *, outbox: MessageOutbox) -> str:
    started_at = datetime.utcnow()
    recipient = outbox.campaign_recipient
    campaign = recipient.campanha
    if campaign.central_status not in CAMPAIGN_ACTIVE_STATUSES:
        outbox.status = "queued" if campaign.central_status == "paused" else "cancelled"
        outbox.locked_at = None
        outbox.locked_by = None
        return "blocked"
    allowed, reason = await communication_allowed(
        session,
        empresa_id=recipient.empresa_id,
        contact_id=recipient.contact_id,
        channel=recipient.channel,
        provider=recipient.provider,
        conexao_id=recipient.conexao_id,
    )
    if not allowed:
        recipient.status = "blocked"
        recipient.error_code = "communication_consent_block"
        recipient.error_detail = reason
        await record_attempt(
            session,
            outbox=outbox,
            result_status="failed",
            error_code="communication_consent_block",
            error_detail=reason,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        await _refresh_campaign_completion(session, empresa_id=campaign.empresa_id, campaign_id=campaign.id)
        return "blocked"

    connection = (
        await session.execute(select(Conexao).where(Conexao.empresa_id == outbox.empresa_id, Conexao.id == outbox.conexao_id))
    ).scalars().first()
    if not connection or not _is_meta_official(connection):
        recipient.status = "failed"
        recipient.failed_at = datetime.utcnow()
        recipient.error_code = "invalid_sender_connection"
        recipient.error_detail = "campaign_sender_must_be_meta_official"
        await record_attempt(
            session,
            outbox=outbox,
            result_status="failed",
            error_code="invalid_sender_connection",
            error_detail="campaign_sender_must_be_meta_official",
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        await _refresh_campaign_completion(session, empresa_id=campaign.empresa_id, campaign_id=campaign.id)
        return "failed"

    if campaign_allowlist_enforced(connection) and not campaign_real_send_allowed(recipient.recipient_address):
        recipient.status = "blocked"
        recipient.error_code = "campaign_allowlist_block"
        recipient.error_detail = "recipient_not_in_campaign_real_send_allowlist"
        await record_attempt(
            session,
            outbox=outbox,
            result_status="failed",
            error_code="campaign_allowlist_block",
            error_detail="recipient_not_in_campaign_real_send_allowlist",
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        await _refresh_campaign_completion(session, empresa_id=campaign.empresa_id, campaign_id=campaign.id)
        return "blocked"

    phone_number_id = str(getattr(connection, "phone_number_id", "") or "").strip()
    if not await acquire_campaign_send_rate_slot(
        phone_number_id=phone_number_id,
        max_mps=_configured_connection_mps(connection),
        now=datetime.utcnow(),
    ):
        outbox.status = "retry"
        outbox.available_at = datetime.utcnow() + timedelta(seconds=1)
        outbox.locked_at = None
        outbox.locked_by = None
        recipient.status = "retry"
        return "retry"

    payload = dict(outbox.payload_json or {})
    template_payload = payload.get("template_payload") if isinstance(payload.get("template_payload"), dict) else {}
    try:
        campaign.central_status = "running"
        recipient.processing_started_at = recipient.processing_started_at or started_at
        result = await _provider_for_connection(connection).send_template(
            ChannelOutboundMessage(
                empresa_id=str(outbox.empresa_id),
                connection_id=str(outbox.conexao_id),
                channel=Channel.WHATSAPP,
                provider=Provider.META_WHATSAPP,
                destination=recipient.recipient_address,
                message_type="template",
                text="",
                metadata={"campaign_id": str(campaign.id), "campaign_recipient_id": str(recipient.id)},
                idempotency_key=outbox.idempotency_key,
            ),
            template_payload=template_payload,
        )
        external_id = str(result.get("external_message_id") or "").strip() or None
        if not external_id:
            raise MetaWhatsAppError("Meta WhatsApp aceitou HTTP mas nao retornou wamid.")
        recipient.status = "sent"
        recipient.sent_at = recipient.sent_at or datetime.utcnow()
        recipient.external_message_id = external_id
        # Persist the provider identifier on the outbox before materializing
        # history.  record_attempt() repeats this assignment, but keeping the
        # two records aligned before the helper runs makes the local contract
        # explicit and testable.
        outbox.external_message_id = external_id
        recipient.error_code = None
        recipient.error_detail = None
        campaign_history = await _materialize_campaign_history(
            session,
            outbox=outbox,
            recipient=recipient,
            external_message_id=external_id,
            template_payload=template_payload,
            history_rendering=payload.get("history_rendering") if isinstance(payload.get("history_rendering"), dict) else None,
        )
        try:
            # The provider has already accepted the message. Keep optional
            # attribution enrichment inside a savepoint so its failure cannot
            # roll back the durable sent/outbox state and cause a resend.
            async with session.begin_nested():
                from app.services.attribution_ledger import record_campaign_touch

                await record_campaign_touch(
                    session,
                    event_kind="campaign_sent",
                    empresa_id=recipient.empresa_id,
                    recipient_id=recipient.id,
                    campaign_id=recipient.campanha_id,
                    contact_id=recipient.contact_id,
                    conversation_id=recipient.conversation_id,
                    lead_id=recipient.lead_id,
                    connection_id=recipient.conexao_id,
                    provider=recipient.provider,
                    channel=recipient.channel,
                    external_message_id=external_id,
                    occurred_at=recipient.sent_at,
                    message_id=campaign_history.id,
                )
        except Exception as attribution_exc:
            logger.warning(
                "event=ATTRIBUTION_CAMPAIGN_SENT_CAPTURE_FAILED exception_type=%s recovery=campaign_recipient_backfill",
                type(attribution_exc).__name__,
            )
        await record_attempt(
            session,
            outbox=outbox,
            result_status="sent",
            external_message_id=external_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        await _refresh_campaign_completion(session, empresa_id=campaign.empresa_id, campaign_id=campaign.id)
        return "sent"
    except MetaWhatsAppError as exc:
        error_code, error_class = _classify_meta_send_error(exc)
        retryable = error_class in {"RATE_LIMIT", "TRANSIENT"} and int(outbox.attempts or 0) + 1 < int(outbox.max_attempts or MAX_SEND_ATTEMPTS)
        recipient.status = "retry" if retryable else "failed"
        if retryable:
            recipient.scheduled_at = _jittered_retry_at(attempts=int(outbox.attempts or 0) + 1)
        else:
            recipient.failed_at = datetime.utcnow()
        recipient.error_code = error_code
        recipient.error_detail = sanitize_error_detail(exc)
        await record_attempt(
            session,
            outbox=outbox,
            result_status="failed",
            error_code=error_code,
            error_detail=exc,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        if retryable and outbox.status == "retry":
            outbox.available_at = recipient.scheduled_at or outbox.available_at
        await _refresh_campaign_completion(session, empresa_id=campaign.empresa_id, campaign_id=campaign.id)
        return "retry" if retryable else "failed"


async def _refresh_campaign_completion(session: AsyncSession, *, empresa_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
    campaign = await _campaign_or_error(session, empresa_id=empresa_id, campaign_id=campaign_id)
    if campaign.central_status in {"paused", "cancelled", "failed"}:
        return
    rows = (
        await session.execute(
            select(CampaignRecipient.status, func.count())
            .where(CampaignRecipient.empresa_id == empresa_id, CampaignRecipient.campanha_id == campaign_id)
            .group_by(CampaignRecipient.status)
        )
    ).all()
    if not rows:
        campaign.central_status = "failed"
        campaign.failed_at = datetime.utcnow()
        campaign.error_code = "no_recipients"
        campaign.error_detail = "campaign_start_materialized_no_recipients"
        return
    counts = {str(status): int(count) for status, count in rows}
    active = sum(counts.get(status, 0) for status in ("queued", "retry", "locked"))
    if active == 0:
        campaign.central_status = "completed"
        campaign.completed_at = campaign.completed_at or datetime.utcnow()


async def campaign_worker_loop() -> None:
    interval = max(1, int(os.getenv("CAMPAIGN_WORKER_INTERVAL_SECONDS", "1")))
    while True:
        try:
            await process_campaign_outbox_once()
        except Exception:
            # Startup workers must never crash the API process.
            logger.exception("campaign outbox processing failed")
        await asyncio.sleep(interval)
