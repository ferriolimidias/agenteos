from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.outbox_service import deterministic_idempotency_key, sanitize_error_detail
from db.models import CampaignRecipient, FollowupExecution, MensagemHistorico, MessageOutbox, MessageStatusEvent


STATUS_RANK = {
    "received": 5,
    "queued": 10,
    "sent": 20,
    "delivered": 30,
    "read": 40,
    "failed": 90,
    "deleted": 100,
    "unknown": 0,
}


def message_status_idempotency_key(
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    provider: str,
    channel: str,
    external_message_id: str | None,
    external_event_id: str,
    status: str,
) -> str:
    status_norm = normalize_delivery_status(status)
    return deterministic_idempotency_key(
        "status",
        empresa_id,
        conexao_id,
        provider,
        channel,
        external_message_id,
        external_event_id,
        status_norm,
    )


def normalize_delivery_status(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "accepted": "sent",
        "enviado": "sent",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "failed": "failed",
        "error": "failed",
        "received": "received",
        "message": "received",
        "deleted": "deleted",
    }
    return aliases.get(raw, "unknown")


def should_advance_status(current: str | None, incoming: str | None) -> bool:
    current_norm = normalize_delivery_status(current)
    incoming_norm = normalize_delivery_status(incoming)
    if incoming_norm == "unknown":
        return False
    if current_norm == "read" and incoming_norm in {"sent", "delivered"}:
        return False
    if current_norm == "delivered" and incoming_norm == "sent":
        return False
    if incoming_norm == "failed" and current_norm in {"read", "delivered"}:
        return False
    return STATUS_RANK[incoming_norm] >= STATUS_RANK[current_norm]


async def record_message_status_event(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    provider: str,
    channel: str,
    external_message_id: str | None,
    external_event_id: str,
    status: str,
    occurred_at: datetime | None = None,
    error_code: str | None = None,
    error_detail: Any = None,
) -> MessageStatusEvent:
    status_norm = normalize_delivery_status(status)
    idem = message_status_idempotency_key(
        empresa_id=empresa_id,
        conexao_id=conexao_id,
        provider=provider,
        channel=channel,
        external_message_id=external_message_id,
        external_event_id=external_event_id,
        status=status_norm,
    )
    empresa_uuid = uuid.UUID(str(empresa_id))
    result = await session.execute(
        select(MessageStatusEvent).where(
            MessageStatusEvent.empresa_id == empresa_uuid,
            MessageStatusEvent.idempotency_key == idem,
        )
    )
    existing = result.scalars().first()
    if existing:
        return existing

    event = MessageStatusEvent(
        empresa_id=empresa_uuid,
        conexao_id=uuid.UUID(str(conexao_id)),
        provider=str(provider),
        channel=str(channel),
        external_message_id=external_message_id,
        external_event_id=str(external_event_id),
        status=status_norm,
        occurred_at=occurred_at,
        raw_status=str(status or ""),
        error_code=error_code,
        error_detail=sanitize_error_detail(error_detail),
        idempotency_key=idem,
    )
    session.add(event)

    if external_message_id:
        await _advance_correlated_rows(
            session,
            empresa_id=empresa_uuid,
            provider=str(provider),
            external_message_id=str(external_message_id),
            status=status_norm,
            occurred_at=occurred_at or datetime.utcnow(),
            error_code=error_code,
            error_detail=error_detail,
        )
    return event


async def _advance_correlated_rows(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    provider: str,
    external_message_id: str,
    status: str,
    occurred_at: datetime,
    error_code: str | None,
    error_detail: Any,
) -> None:
    for model, field in (
        (MensagemHistorico, MensagemHistorico.external_message_id),
        (CampaignRecipient, CampaignRecipient.external_message_id),
        (FollowupExecution, FollowupExecution.external_message_id),
        (MessageOutbox, MessageOutbox.external_message_id),
    ):
        result = await session.execute(
            select(model).where(
                model.empresa_id == empresa_id,
                model.provider == provider,
                field == external_message_id,
            )
        )
        for row in result.scalars().all():
            current = getattr(row, "message_status", None) or getattr(row, "status", None)
            if not should_advance_status(current, status):
                continue
            if hasattr(row, "message_status"):
                row.message_status = status
                row.provider_status = status
            else:
                row.status = status
            if status == "sent" and hasattr(row, "sent_at"):
                row.sent_at = getattr(row, "sent_at", None) or occurred_at
            elif status == "delivered" and hasattr(row, "delivered_at"):
                row.delivered_at = getattr(row, "delivered_at", None) or occurred_at
            elif status == "read" and hasattr(row, "read_at"):
                row.read_at = getattr(row, "read_at", None) or occurred_at
            elif status == "failed" and hasattr(row, "failed_at"):
                row.failed_at = getattr(row, "failed_at", None) or occurred_at
                if hasattr(row, "error_code"):
                    row.error_code = error_code
                    row.error_detail = sanitize_error_detail(error_detail)
