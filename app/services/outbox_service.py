from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_sanitizer import sanitize_for_log
from app.services.channels.types import normalize_channel, normalize_provider
from db.models import (
    CampaignRecipient,
    CommunicationConsent,
    FollowupExecution,
    MessageAttempt,
    MessageOutbox,
)


TRANSIENT_ERROR_CODES = {"timeout", "rate_limited", "transport_error", "provider_5xx"}


def deterministic_idempotency_key(*parts: Any) -> str:
    material = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sanitize_error_detail(value: Any) -> str | None:
    text = str(sanitize_for_log(value or "")).strip()
    return text[:500] if text else None


async def communication_allowed(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    contact_id: uuid.UUID | str,
    channel: str,
    provider: str,
    conexao_id: uuid.UUID | str | None = None,
) -> tuple[bool, str]:
    conditions = [
        CommunicationConsent.empresa_id == uuid.UUID(str(empresa_id)),
        CommunicationConsent.contact_id == uuid.UUID(str(contact_id)),
        CommunicationConsent.channel == normalize_channel(channel).value,
        CommunicationConsent.provider == normalize_provider(provider).value,
    ]
    if conexao_id:
        conditions.append(CommunicationConsent.conexao_id == uuid.UUID(str(conexao_id)))
    result = await session.execute(select(CommunicationConsent).where(*conditions).limit(1))
    consent = result.scalars().first()
    status = str(getattr(consent, "status", "unknown") or "unknown")
    if status in {"opted_out", "opt_out", "blocked", "revoked"}:
        return False, status
    return True, status


async def upsert_consent(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    contact_id: uuid.UUID | str,
    channel: str,
    provider: str,
    status: str,
    lead_id: uuid.UUID | str | None = None,
    conexao_id: uuid.UUID | str | None = None,
    source: str = "manual",
    proof_reference: str | None = None,
    reason: str | None = None,
    recorded_by_user_id: uuid.UUID | str | None = None,
    at: datetime | None = None,
) -> CommunicationConsent:
    allowed = {"unknown", "opted_in", "opted_out", "blocked", "transactional_only"}
    if status not in allowed:
        raise ValueError("status de consentimento invalido.")
    empresa_uuid = uuid.UUID(str(empresa_id))
    contact_uuid = uuid.UUID(str(contact_id))
    conexao_uuid = uuid.UUID(str(conexao_id)) if conexao_id else None
    ch = normalize_channel(channel).value
    pr = normalize_provider(provider).value
    result = await session.execute(
        select(CommunicationConsent).where(
            CommunicationConsent.empresa_id == empresa_uuid,
            CommunicationConsent.contact_id == contact_uuid,
            CommunicationConsent.channel == ch,
            CommunicationConsent.provider == pr,
            CommunicationConsent.conexao_id == conexao_uuid,
        )
    )
    row = result.scalars().first()
    if not row:
        row = CommunicationConsent(
            empresa_id=empresa_uuid,
            contact_id=contact_uuid,
            conexao_id=conexao_uuid,
            channel=ch,
            provider=pr,
        )
        session.add(row)
    row.status = status
    row.lead_id = uuid.UUID(str(lead_id)) if lead_id else row.lead_id
    row.source = source or "manual"
    row.proof_reference = sanitize_error_detail(proof_reference)
    row.reason = sanitize_error_detail(reason)
    row.recorded_by_user_id = uuid.UUID(str(recorded_by_user_id)) if recorded_by_user_id else None
    at = at or datetime.utcnow()
    if status == "opted_in":
        row.consented_at = at
        row.revoked_at = None
        row.blocked_at = None
    elif status == "opted_out":
        row.revoked_at = at
    elif status == "blocked":
        row.blocked_at = at
    return row


async def enqueue_message(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    provider: str,
    channel: str,
    operation_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    campaign_recipient_id: uuid.UUID | str | None = None,
    followup_execution_id: uuid.UUID | str | None = None,
    priority: int = 100,
    available_at: datetime | None = None,
    max_attempts: int = 3,
) -> MessageOutbox:
    empresa_uuid = uuid.UUID(str(empresa_id))
    result = await session.execute(
        select(MessageOutbox).where(
            MessageOutbox.empresa_id == empresa_uuid,
            MessageOutbox.idempotency_key == idempotency_key,
        )
    )
    existing = result.scalars().first()
    if existing:
        return existing
    outbox = MessageOutbox(
        empresa_id=empresa_uuid,
        conexao_id=uuid.UUID(str(conexao_id)),
        provider=normalize_provider(provider).value,
        channel=normalize_channel(channel).value,
        operation_type=operation_type,
        payload_json=dict(payload or {}),
        idempotency_key=idempotency_key,
        priority=int(priority),
        available_at=available_at or datetime.utcnow(),
        max_attempts=max(1, int(max_attempts)),
        campaign_recipient_id=uuid.UUID(str(campaign_recipient_id)) if campaign_recipient_id else None,
        followup_execution_id=uuid.UUID(str(followup_execution_id)) if followup_execution_id else None,
    )
    session.add(outbox)
    return outbox


async def claim_outbox_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    limit: int = 10,
    now: datetime | None = None,
    lock_timeout_seconds: int = 300,
) -> list[MessageOutbox]:
    now = now or datetime.utcnow()
    stale_before = now - timedelta(seconds=max(30, int(lock_timeout_seconds)))
    stmt = (
        select(MessageOutbox)
        .where(
            MessageOutbox.status.in_(["queued", "retry"]),
            MessageOutbox.available_at <= now,
            (MessageOutbox.locked_at.is_(None)) | (MessageOutbox.locked_at < stale_before),
        )
        .order_by(MessageOutbox.priority.asc(), MessageOutbox.available_at.asc(), MessageOutbox.created_at.asc())
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    for row in rows:
        row.status = "locked"
        row.locked_at = now
        row.locked_by = str(worker_id or "")[:120]
    return rows


def next_retry_at(*, attempts: int, now: datetime | None = None) -> datetime:
    now = now or datetime.utcnow()
    delay_seconds = min(3600, 30 * (2 ** max(0, int(attempts) - 1)))
    return now + timedelta(seconds=delay_seconds)


async def record_attempt(
    session: AsyncSession,
    *,
    outbox: MessageOutbox,
    result_status: str,
    external_message_id: str | None = None,
    error_code: str | None = None,
    error_detail: Any = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> MessageAttempt:
    finished_at = finished_at or datetime.utcnow()
    started_at = started_at or finished_at
    outbox.attempts = int(outbox.attempts or 0) + 1
    attempt = MessageAttempt(
        outbox_id=outbox.id,
        empresa_id=outbox.empresa_id,
        provider=outbox.provider,
        conexao_id=outbox.conexao_id,
        attempt_number=outbox.attempts,
        started_at=started_at,
        finished_at=finished_at,
        result_status=result_status,
        external_message_id=external_message_id,
        error_code=error_code,
        error_detail=sanitize_error_detail(error_detail),
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
    )
    session.add(attempt)
    outbox.external_message_id = external_message_id or outbox.external_message_id
    if result_status in {"accepted", "sent", "completed", "mocked"}:
        outbox.status = "completed"
        outbox.completed_at = finished_at
        outbox.locked_at = None
        outbox.locked_by = None
    else:
        outbox.last_error_code = error_code
        outbox.last_error_detail = sanitize_error_detail(error_detail)
        if (error_code in TRANSIENT_ERROR_CODES) and outbox.attempts < int(outbox.max_attempts or 1):
            outbox.status = "retry"
            outbox.available_at = next_retry_at(attempts=outbox.attempts, now=finished_at)
        else:
            outbox.status = "failed"
            outbox.completed_at = finished_at
        outbox.locked_at = None
        outbox.locked_by = None
    return attempt


async def dispatch_outbox_now(
    session: AsyncSession,
    *,
    outbox: MessageOutbox,
    identificador_origem: str,
    texto: str,
    dispatcher=None,
    segment_whatsapp: bool = False,
    message_type: str = "text",
    template_payload: dict[str, Any] | None = None,
    media: dict[str, Any] | None = None,
    send_as_voice: bool = False,
) -> bool:
    from datetime import datetime

    if dispatcher is None:
        from app.services.channel_factory import despachar_mensagem

        dispatcher = despachar_mensagem

    started_at = datetime.utcnow()
    try:
        dispatch_result = await dispatcher(
            canal=str(outbox.channel or "whatsapp"),
            identificador_origem=identificador_origem,
            texto=texto,
            conexao_id=str(outbox.conexao_id),
            empresa_id=str(outbox.empresa_id),
            segment_whatsapp=segment_whatsapp,
            message_type=message_type,
            template_payload=template_payload,
            media=media,
            send_as_voice=send_as_voice,
            return_details=True,
        )
        external_message_id = None
        if isinstance(dispatch_result, dict):
            enviado = bool(dispatch_result.get("sent"))
            external_ids = dispatch_result.get("external_message_ids")
            if isinstance(external_ids, list):
                external_message_id = next(
                    (str(value).strip() for value in external_ids if str(value or "").strip()),
                    None,
                )
        else:
            enviado = bool(dispatch_result)
    except Exception as exc:
        await record_attempt(
            session,
            outbox=outbox,
            result_status="failed",
            error_code="dispatch_exception",
            error_detail=exc,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        return False

    if enviado:
        await record_attempt(
            session,
            outbox=outbox,
            result_status="sent",
            external_message_id=external_message_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        return True

    await record_attempt(
        session,
        outbox=outbox,
        result_status="failed",
        error_code="dispatch_returned_false",
        error_detail="dispatcher returned false",
        started_at=started_at,
        finished_at=datetime.utcnow(),
    )
    return False


async def upsert_campaign_recipient(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    campanha_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    provider: str,
    channel: str,
    lead_id: uuid.UUID | str | None,
    contact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | str | None,
    recipient_address: str,
    template_id: uuid.UUID | str | None,
    parameters: dict[str, Any] | None,
    scheduled_at: datetime | None,
    idempotency_key: str,
) -> CampaignRecipient:
    empresa_uuid = uuid.UUID(str(empresa_id))
    result = await session.execute(
        select(CampaignRecipient).where(
            CampaignRecipient.empresa_id == empresa_uuid,
            CampaignRecipient.idempotency_key == idempotency_key,
        )
    )
    row = result.scalars().first()
    if row:
        return row
    allowed, reason = await communication_allowed(
        session,
        empresa_id=empresa_uuid,
        contact_id=contact_id,
        channel=channel,
        provider=provider,
        conexao_id=conexao_id,
    )
    row = CampaignRecipient(
        empresa_id=empresa_uuid,
        campanha_id=uuid.UUID(str(campanha_id)),
        conexao_id=uuid.UUID(str(conexao_id)),
        provider=normalize_provider(provider).value,
        channel=normalize_channel(channel).value,
        lead_id=uuid.UUID(str(lead_id)) if lead_id else None,
        contact_id=uuid.UUID(str(contact_id)),
        conversation_id=uuid.UUID(str(conversation_id)) if conversation_id else None,
        recipient_address=str(recipient_address or "").strip(),
        template_id=uuid.UUID(str(template_id)) if template_id else None,
        parameters_json=dict(parameters or {}),
        scheduled_at=scheduled_at,
        idempotency_key=idempotency_key,
        status="blocked" if not allowed else "queued",
        error_code="communication_consent_block" if not allowed else None,
        error_detail=reason if not allowed else None,
    )
    session.add(row)
    return row


async def upsert_followup_execution(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    followup_id: uuid.UUID | str,
    lead_id: uuid.UUID | str,
    contact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    provider: str,
    channel: str,
    trigger: str,
    scheduled_at: datetime,
    idempotency_key: str,
    template_id: uuid.UUID | str | None = None,
) -> FollowupExecution:
    empresa_uuid = uuid.UUID(str(empresa_id))
    result = await session.execute(
        select(FollowupExecution).where(
            FollowupExecution.empresa_id == empresa_uuid,
            FollowupExecution.idempotency_key == idempotency_key,
        )
    )
    row = result.scalars().first()
    if row:
        return row
    allowed, reason = await communication_allowed(
        session,
        empresa_id=empresa_uuid,
        contact_id=contact_id,
        channel=channel,
        provider=provider,
        conexao_id=conexao_id,
    )
    row = FollowupExecution(
        empresa_id=empresa_uuid,
        followup_id=uuid.UUID(str(followup_id)),
        lead_id=uuid.UUID(str(lead_id)),
        contact_id=uuid.UUID(str(contact_id)),
        conversation_id=uuid.UUID(str(conversation_id)),
        conexao_id=uuid.UUID(str(conexao_id)),
        provider=normalize_provider(provider).value,
        channel=normalize_channel(channel).value,
        trigger=trigger,
        scheduled_at=scheduled_at,
        template_id=uuid.UUID(str(template_id)) if template_id else None,
        idempotency_key=idempotency_key,
        status="stopped" if not allowed else "scheduled",
        stop_reason=reason if not allowed else None,
    )
    session.add(row)
    return row
