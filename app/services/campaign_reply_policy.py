from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CampanhaDisparo, MensagemHistorico


def _context_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "message_id", "wamid", "referred_message_id", "reply_to_message_id"} and isinstance(item, str) and item.strip():
                found.add(item.strip())
            found.update(_context_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_context_ids(item))
    return found


async def resolve_campaign_reply_policy(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conversation_id: uuid.UUID,
    event_metadata: dict[str, Any] | None = None,
) -> bool | None:
    """Return campaign policy, with explicit reply context taking priority."""
    rows = list((await session.execute(select(MensagemHistorico).where(
        MensagemHistorico.empresa_id == empresa_id,
        MensagemHistorico.channel_conversation_id == conversation_id,
        MensagemHistorico.direction == "outbound",
    ).order_by(MensagemHistorico.criado_em.desc(), MensagemHistorico.id.desc()).limit(100))).scalars().all())
    context_ids = _context_ids(event_metadata or {})
    has_explicit_context = bool(context_ids)
    selected = next((row for row in rows if row.external_message_id in context_ids and row.origem_mensagem == "campaign"), None)
    if has_explicit_context and selected is None:
        # An explicit reply to a non-campaign message must not fall through to
        # an unrelated older campaign in the same conversation.
        return None
    if selected is None:
        selected = rows[0] if rows else None
    if selected is None or selected.origem_mensagem != "campaign":
        return None
    metadata = dict(selected.metadata_json or {})
    campaign_id = metadata.get("campaign_id")
    if not campaign_id:
        return None
    try:
        campaign_uuid = uuid.UUID(str(campaign_id))
    except (TypeError, ValueError):
        return None
    campaign = (await session.execute(select(CampanhaDisparo).where(
        CampanhaDisparo.empresa_id == empresa_id,
        CampanhaDisparo.id == campaign_uuid,
    ))).scalars().first()
    if not campaign:
        return None
    return True if campaign.ai_reply_enabled is None else bool(campaign.ai_reply_enabled)
