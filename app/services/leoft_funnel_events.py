from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_sanitizer import sanitize_for_log
from db.models import LeoFTIntegrationEvent


FUNNEL_INTEGRATION = "leoft_funnel"


async def record_leoft_funnel_event(
    db: AsyncSession,
    *,
    empresa_id: Any,
    lead_id: Any | None,
    event_type: str,
    source: str,
    anamnese_id: str | None = None,
    checkout_reference: str | None = None,
    payment_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    clean_event = str(event_type or "").strip()
    if not clean_event:
        return False
    scope = ":".join(
        [
            str(empresa_id or ""),
            str(lead_id or "anonymous"),
            str(anamnese_id or ""),
            str(checkout_reference or ""),
            str(payment_id or ""),
            clean_event,
        ]
    )
    idempotency_key = hashlib.sha256(scope.encode()).hexdigest()
    existing = await db.scalar(
        select(LeoFTIntegrationEvent.id).where(
            LeoFTIntegrationEvent.integration == FUNNEL_INTEGRATION,
            LeoFTIntegrationEvent.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return False
    payload = sanitize_for_log(
        {
            "empresa_id": str(empresa_id or ""),
            "lead_id": str(lead_id or "") if lead_id else None,
            "event_type": clean_event,
            "source": str(source or "")[:80],
            "anamnese_id": str(anamnese_id or "")[:160] or None,
            "checkout_reference": str(checkout_reference or "")[:160] or None,
            "payment_id": str(payment_id or "")[:160] or None,
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
    )
    db.add(
        LeoFTIntegrationEvent(
            direction="internal",
            integration=FUNNEL_INTEGRATION,
            event_type=clean_event,
            external_event_id=str(payment_id or checkout_reference or anamnese_id or lead_id or "")[:160] or None,
            idempotency_key=idempotency_key,
            request_payload_sanitized=payload,
            response_status=200,
            response_payload_sanitized={"ok": True},
            status="processed",
            processed_at=occurred_at or datetime.utcnow(),
        )
    )
    return True
