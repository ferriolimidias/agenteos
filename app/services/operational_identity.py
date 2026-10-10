from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.channels.resolver import context_from_connection
from app.services.channels.types import Channel, Provider, normalize_channel, normalize_provider
from app.services.whatsapp_address import normalize_whatsapp_address
from db.models import ChannelContact, ChannelConversation, Conexao, CRMLead


def sanitize_address(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@s.whatsapp.net" in text:
        text = text.replace("@s.whatsapp.net", "")
    if "@g.us" in text:
        text = text.replace("@g.us", "")
    digits = re.sub(r"\D", "", text)
    return digits or text[:128]


def canonical_operational_address(value: str | None, *, channel: str | Channel) -> str:
    """Return the stable address used by operational contact/conversation keys."""
    if normalize_channel(channel) != Channel.WHATSAPP:
        return sanitize_address(value)

    normalized = normalize_whatsapp_address(value, default_country="BR")
    digits = normalized.digits
    # Brazilian mobile numbers historically appear with 8 or 9 subscriber
    # digits.  Canonicalize only the unambiguous old mobile form; landlines
    # (subscriber prefix 2-5) remain unchanged.
    if digits.startswith("55") and len(digits) == 12 and digits[4] in "6789":
        digits = f"{digits[:4]}9{digits[4:]}"
    return digits or sanitize_address(value)


def deterministic_conversation_key(*, channel: str | Channel, provider: str | Provider, external_contact_id: str) -> str:
    ch = normalize_channel(channel)
    pr = normalize_provider(provider)
    address = (
        canonical_operational_address(external_contact_id, channel=ch)
        if ch == Channel.WHATSAPP
        else str(external_contact_id or "").strip()
    )
    return ":".join(
        (
            ch.value,
            pr.value,
            address,
        )
    )


def _canonical_conversation_key_address(value: str | None, *, channel: str | Channel) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    address = text.rsplit(":", 1)[-1]
    if normalize_channel(channel) == Channel.WHATSAPP:
        return canonical_operational_address(address, channel=channel)
    return address


@dataclass(frozen=True)
class OperationalConversation:
    empresa_id: uuid.UUID
    conexao_id: uuid.UUID
    channel: Channel
    provider: Provider
    contact: ChannelContact
    conversation: ChannelConversation
    lead: CRMLead | None


async def resolve_connection_for_tenant(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
) -> Conexao:
    empresa_uuid = uuid.UUID(str(empresa_id))
    conexao_uuid = uuid.UUID(str(conexao_id))
    result = await session.execute(
        select(Conexao).where(Conexao.id == conexao_uuid, Conexao.empresa_id == empresa_uuid)
    )
    conexao = result.scalars().first()
    if not conexao:
        raise ValueError("Conexao nao encontrada para a empresa informada.")
    return conexao


async def upsert_channel_contact(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    channel: str | Channel,
    provider: str | Provider,
    external_contact_id: str,
    lead_id: uuid.UUID | str | None = None,
    normalized_address: str | None = None,
    phone_e164: str | None = None,
    username: str | None = None,
    display_name: str | None = None,
    profile_photo_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    seen_at: datetime | None = None,
) -> ChannelContact:
    empresa_uuid = uuid.UUID(str(empresa_id))
    conexao_uuid = uuid.UUID(str(conexao_id))
    clean_external = str(external_contact_id or "").strip()
    if not clean_external:
        raise ValueError("external_contact_id obrigatorio.")
    ch = normalize_channel(channel).value
    pr = normalize_provider(provider).value
    seen_at = seen_at or datetime.utcnow()

    canonical_address = canonical_operational_address(clean_external, channel=ch)
    result = await session.execute(
        select(ChannelContact).where(
            ChannelContact.empresa_id == empresa_uuid,
            ChannelContact.conexao_id == conexao_uuid,
            ChannelContact.channel == ch,
            ChannelContact.provider == pr,
            ChannelContact.external_contact_id == clean_external,
        )
    )
    contact = result.scalars().first()
    if not contact and normalize_channel(ch) == Channel.WHATSAPP:
        scoped = await session.execute(
            select(ChannelContact).where(
                ChannelContact.empresa_id == empresa_uuid,
                ChannelContact.conexao_id == conexao_uuid,
                ChannelContact.channel == ch,
                ChannelContact.provider == pr,
            ).order_by(ChannelContact.criado_em.asc())
        )
        for candidate in scoped.scalars().all():
            candidate_address = canonical_operational_address(
                candidate.phone_e164 or candidate.normalized_address or candidate.external_contact_id,
                channel=ch,
            )
            if candidate_address == canonical_address:
                contact = candidate
                break
    if not contact:
        contact = ChannelContact(
            empresa_id=empresa_uuid,
            conexao_id=conexao_uuid,
            channel=ch,
            provider=pr,
            external_contact_id=canonical_address,
            first_seen_at=seen_at,
        )
        session.add(contact)

    if lead_id and not contact.lead_id:
        contact.lead_id = uuid.UUID(str(lead_id))
    contact.normalized_address = canonical_address or normalized_address or contact.normalized_address or sanitize_address(clean_external)
    contact.phone_e164 = phone_e164 or contact.phone_e164 or (canonical_address if ch == Channel.WHATSAPP else None)
    contact.username = username or contact.username
    current_metadata = dict(contact.metadata_json or {})
    current_name_source = str(current_metadata.get("trusted_display_name_source") or "").strip().lower()
    if display_name and (
        not contact.display_name
        or current_name_source not in {"manual", "meta_profile"}
    ):
        contact.display_name = display_name
        contact.metadata_json = {
            **current_metadata,
            "trusted_display_name_source": "meta_profile",
        }
    contact.profile_photo_url = profile_photo_url or contact.profile_photo_url
    contact.metadata_json = {**dict(contact.metadata_json or {}), **dict(metadata or {})}
    contact.last_seen_at = seen_at
    return contact


async def upsert_channel_conversation(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    contact_id: uuid.UUID | str,
    channel: str | Channel,
    provider: str | Provider,
    conversation_key: str,
    external_conversation_id: str | None = None,
    external_thread_id: str | None = None,
    lead_id: uuid.UUID | str | None = None,
    direction: str | None = None,
    message_at: datetime | None = None,
    status: str = "open",
    metadata: dict[str, Any] | None = None,
) -> ChannelConversation:
    empresa_uuid = uuid.UUID(str(empresa_id))
    conexao_uuid = uuid.UUID(str(conexao_id))
    contact_uuid = uuid.UUID(str(contact_id))
    key = str(conversation_key or "").strip()
    if not key:
        raise ValueError("conversation_key obrigatorio.")
    ch = normalize_channel(channel).value
    pr = normalize_provider(provider).value
    message_at = message_at or datetime.utcnow()

    scoped = await session.execute(
        select(ChannelConversation).where(
            ChannelConversation.empresa_id == empresa_uuid,
            ChannelConversation.conexao_id == conexao_uuid,
            ChannelConversation.channel == ch,
            ChannelConversation.provider == pr,
        ).order_by(ChannelConversation.atualizado_em.desc().nullslast(), ChannelConversation.criado_em.desc())
    )
    conversation = None
    key_address = str(key).rsplit(":", 1)[-1]
    canonical_key_address = _canonical_conversation_key_address(key, channel=ch)
    for candidate in scoped.scalars().all():
        candidate_address = str(candidate.conversation_key or "").rsplit(":", 1)[-1]
        canonical_candidate_address = _canonical_conversation_key_address(candidate.conversation_key, channel=ch)
        canonical_candidate_external = _canonical_conversation_key_address(
            candidate.external_conversation_id,
            channel=ch,
        )
        if (
            candidate_address == key_address
            or canonical_candidate_address == canonical_key_address
            or canonical_candidate_external == canonical_key_address
        ):
            conversation = candidate
            break
    created = conversation is None
    if not conversation:
        conversation = ChannelConversation(
            empresa_id=empresa_uuid,
            conexao_id=conexao_uuid,
            contact_id=contact_uuid,
            channel=ch,
            provider=pr,
            conversation_key=key,
            external_conversation_id=external_conversation_id or key,
            status=status or "open",
        )
        session.add(conversation)

    if not created and str(conversation.status or "").strip().lower() in {
        "closed", "concluido", "concluida", "completed", "archived", "arquivado", "finalizado",
    }:
        # Campaign and inbound traffic reopen the same operational thread.
        conversation.status = "open"
    if not created and lead_id and not conversation.lead_id:
        conversation.lead_id = uuid.UUID(str(lead_id))
    if created and lead_id:
        conversation.lead_id = uuid.UUID(str(lead_id))
    if created:
        conversation.contact_id = contact_uuid
    conversation.external_conversation_id = external_conversation_id or conversation.external_conversation_id or key
    conversation.external_thread_id = external_thread_id or conversation.external_thread_id
    existing_metadata = dict(conversation.metadata_json or {})
    incoming_metadata = dict(metadata or {})
    existing_name_source = str(existing_metadata.get("trusted_display_name_source") or "").strip().lower()
    incoming_name_source = str(incoming_metadata.get("trusted_display_name_source") or "").strip().lower()
    if (
        not created
        and incoming_name_source == "meta_profile"
        and existing_name_source in {"manual", "meta_profile"}
    ):
        incoming_metadata.pop("trusted_display_name_source", None)
    merged_metadata = {**existing_metadata, **incoming_metadata}
    if created and bool((metadata or {}).get("campaign_outbound")):
        merged_metadata.setdefault("campaign_created_new_conversation", True)
        merged_metadata.setdefault("trusted_display_name_source", "none")
    conversation.metadata_json = merged_metadata
    conversation.last_message_at = message_at
    if str(direction or "").lower() == "inbound":
        conversation.last_inbound_at = message_at
    elif str(direction or "").lower() == "outbound":
        conversation.last_outbound_at = message_at
    return conversation


async def ensure_operational_conversation(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID | str,
    conexao_id: uuid.UUID | str,
    external_contact_id: str,
    lead: CRMLead | None = None,
    direction: str | None = None,
    message_at: datetime | None = None,
    external_conversation_id: str | None = None,
    external_thread_id: str | None = None,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OperationalConversation:
    conexao = await resolve_connection_for_tenant(session, empresa_id=empresa_id, conexao_id=conexao_id)
    context = context_from_connection(conexao)
    contact = await upsert_channel_contact(
        session,
        empresa_id=empresa_id,
        conexao_id=conexao.id,
        channel=context.channel,
        provider=context.provider,
        external_contact_id=external_contact_id,
        lead_id=getattr(lead, "id", None),
        normalized_address=sanitize_address(external_contact_id),
        phone_e164=sanitize_address(external_contact_id) if context.channel == Channel.WHATSAPP else None,
        display_name=display_name,
        metadata=metadata,
        seen_at=message_at,
    )
    await session.flush()
    canonical_address = canonical_operational_address(external_contact_id, channel=context.channel)
    key = deterministic_conversation_key(
        channel=context.channel,
        provider=context.provider,
        external_contact_id=canonical_address,
    )
    external_identity = key if context.channel == Channel.WHATSAPP else (external_conversation_id or key)
    conversation_metadata = {**dict(metadata or {})}
    if display_name:
        conversation_metadata["trusted_display_name_source"] = "meta_profile"
        conversation_metadata["trusted_display_name"] = str(display_name).strip()[:160]
    conversation = await upsert_channel_conversation(
        session,
        empresa_id=empresa_id,
        conexao_id=conexao.id,
        contact_id=contact.id,
        channel=context.channel,
        provider=context.provider,
        conversation_key=key,
        external_conversation_id=external_identity,
        external_thread_id=external_thread_id,
        lead_id=getattr(lead, "id", None),
        direction=direction,
        message_at=message_at,
        metadata=conversation_metadata,
    )
    if display_name and str(conversation.contact_id) != str(contact.id):
        bound_contact = (
            await session.execute(
                select(ChannelContact).where(
                    ChannelContact.id == conversation.contact_id,
                    ChannelContact.empresa_id == uuid.UUID(str(empresa_id)),
                )
            )
        ).scalars().first()
        if bound_contact:
            bound_metadata = dict(bound_contact.metadata_json or {})
            bound_name_source = str(bound_metadata.get("trusted_display_name_source") or "").strip().lower()
            if bound_name_source != "manual":
                bound_contact.display_name = str(display_name).strip()[:160]
                bound_contact.metadata_json = {
                    **bound_metadata,
                    "trusted_display_name_source": "meta_profile",
                }
    await session.flush()
    return OperationalConversation(
        empresa_id=uuid.UUID(str(empresa_id)),
        conexao_id=conexao.id,
        channel=context.channel,
        provider=context.provider,
        contact=contact,
        conversation=conversation,
        lead=lead,
    )
